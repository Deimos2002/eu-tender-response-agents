"""OpenAI-compatible chat client with tool calling, an on-disk response cache and token accounting.

The cache (SQLite in .cache/, git-ignored) makes evaluation reruns free and reproducible. Keys come from the
environment and are never logged.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from tw import observe
from tw.config import CACHE, load_dotenv

PROVIDERS = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
}
# USD per million tokens (input, output), list prices used to report cost per tender.
PRICES = {"gpt-5-mini": (0.25, 2.00), "gpt-5-nano": (0.05, 0.40), "ministral-8b": (0.10, 0.10), "gpt-oss-120b": (0.15, 0.60)}
REASONING_MODEL = re.compile(r"^(gpt-5|o\d)")
DAILY_QUOTA = re.compile(r"per day|\(TPD\)|\(RPD\)|insufficient_quota", re.I)


class LLMError(RuntimeError):
    pass


@dataclass
class Completion:
    message: dict              # assistant message: {"role", "content", "tool_calls"?}
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    cached: bool = False


def price(model: str, input_tokens: int, output_tokens: int) -> float:
    for prefix, (pin, pout) in PRICES.items():
        if model.startswith(prefix) or prefix in model:
            return round((input_tokens * pin + output_tokens * pout) / 1e6, 6)
    return 0.0


class ChatLLM:
    def __init__(self, provider: str, model: str, *, timeout: float = 180, max_retries: int = 4, use_cache: bool = True):
        if provider not in PROVIDERS:
            raise LLMError(f"unknown provider {provider!r}; choose from {sorted(PROVIDERS)}")
        self.provider, self.model = provider, model
        self.base_url, key_var = PROVIDERS[provider]
        self._key = os.environ.get(key_var, "")
        # Cache-only mode replays past runs and never calls the API: a cache miss is an error, not a paid call.
        self.cache_only = os.environ.get("TW_CACHE_ONLY", "") == "1"
        if self.cache_only and not use_cache:
            raise LLMError("TW_CACHE_ONLY=1 needs the response cache")
        if not self._key and not self.cache_only:
            raise LLMError(f"{key_var} is not set (add it to .env)")
        self.timeout, self.max_retries = timeout, max_retries
        self._db = None
        if use_cache:
            CACHE.mkdir(exist_ok=True)
            self._db = sqlite3.connect(CACHE / "llm.sqlite", check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT)")

    def complete(self, messages: list[dict], *, tools: list[dict] | None = None, max_tokens: int = 4000,
                 force_tool: str | None = None) -> Completion:
        """force_tool: name of the tool the model must call (structured output for a graph node).

        With Langfuse configured, each call is one generation (messages, answer, tokens, cost, cache hit)."""
        with observe.observation(f"{self.provider}.chat", as_type="generation", model=self.model, input=messages,
                                 model_parameters={"max_tokens": max_tokens}) as gen:
            comp = self._complete(messages, tools=tools, max_tokens=max_tokens, force_tool=force_tool)
            observe.update(gen, output=comp.message, model=comp.model,
                           usage_details={"input": comp.input_tokens, "output": comp.output_tokens},
                           cost_details={"total": price(comp.model, comp.input_tokens, comp.output_tokens)},
                           metadata={"cached": comp.cached, "original_latency_s": comp.latency_s,
                                     "tools": [t["function"]["name"] for t in tools or []], "force_tool": force_tool})
            return comp

    def _complete(self, messages: list[dict], *, tools: list[dict] | None, max_tokens: int,
                  force_tool: str | None) -> Completion:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.provider == "openai" and REASONING_MODEL.match(self.model):
            payload |= {"max_completion_tokens": max_tokens,
                        "reasoning_effort": os.environ.get("TW_REASONING_EFFORT", "low")}
        else:
            payload |= {"max_tokens": max_tokens, "temperature": 0.0}
        if tools:
            payload |= {"tools": tools,
                        "tool_choice": {"type": "function", "function": {"name": force_tool}} if force_tool else "auto"}
        key = hashlib.sha256(json.dumps([self.provider, payload], sort_keys=True).encode()).hexdigest()
        if self._db is not None:
            row = self._db.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
            if row:
                return Completion(**json.loads(row[0]) | {"cached": True})
        if self.cache_only:
            raise LLMError("cache miss with TW_CACHE_ONLY=1: this call was never made before")
        t0, delay = time.monotonic(), 2.0
        for attempt in range(self.max_retries + 1):
            try:
                r = httpx.post(f"{self.base_url}/chat/completions", json=payload, timeout=self.timeout,
                               headers={"Authorization": f"Bearer {self._key}"})
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise LLMError(f"{self.provider} unreachable: {exc}") from exc
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 429 and DAILY_QUOTA.search(r.text):
                raise LLMError(f"{self.provider} quota exhausted: {r.text[:200]}")
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                wait = min(float(r.headers.get("retry-after") or delay), 60)
                print(f"  [{self.provider}] HTTP {r.status_code}, retrying in {wait:.0f}s", file=sys.stderr, flush=True)
                time.sleep(wait)
                delay *= 2
                continue
            if r.status_code >= 400:
                raise LLMError(f"{self.provider} HTTP {r.status_code}: {r.text[:300]}")
            body = r.json()
            msg = body["choices"][0]["message"]
            message = {"role": "assistant", "content": msg.get("content") or ""}
            if msg.get("tool_calls"):
                message["tool_calls"] = [{"id": c["id"], "type": "function",
                                          "function": {"name": c["function"]["name"], "arguments": c["function"]["arguments"]}}
                                         for c in msg["tool_calls"]]
            usage = body.get("usage") or {}
            comp = Completion(message=message, model=body.get("model", self.model),
                              input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0),
                              latency_s=round(time.monotonic() - t0, 2))
            if self._db is not None:
                self._db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?)",
                                 (key, json.dumps({k: v for k, v in comp.__dict__.items() if k != "cached"})))
                self._db.commit()
            return comp
        raise LLMError(f"{self.provider}: retries exhausted")


@dataclass
class ScriptedLLM:
    """Offline stand-in for tests: ``respond(messages, tools) -> assistant message``."""

    respond: Callable[[list[dict], list[dict] | None], dict]
    provider: str = "fake"
    model: str = "fake-1"
    calls: list = field(default_factory=list)

    def complete(self, messages: list[dict], *, tools: list[dict] | None = None, max_tokens: int = 4000,
                 force_tool: str | None = None) -> Completion:
        self.calls.append(messages)
        return Completion(message=self.respond(messages, tools), model=self.model, input_tokens=100, output_tokens=50)


def from_env() -> ChatLLM:
    load_dotenv()
    return ChatLLM(os.environ.get("TW_PROVIDER", "openai"), os.environ.get("TW_MODEL", "gpt-5-mini"))

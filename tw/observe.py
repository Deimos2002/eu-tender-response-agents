"""Optional Langfuse tracing: model calls, tool calls and graph steps.

Every function here is a no-op unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set and the `langfuse`
extra is installed (`pip install -e .[langfuse]`), so tests and ordinary runs need nothing. Observations nest
through OpenTelemetry context: a model call made inside a graph step appears under that step, and a step under
the run that started it (see tw/evals/langfuse_sync.py).
"""
from __future__ import annotations

import contextlib
import functools
import os
import sys
from typing import Any

_client: Any = None
_checked = False
MAX_TOOL_OUTPUT = 4000


def client():
    """The Langfuse client, or None when tracing is off."""
    global _client, _checked
    if not _checked:
        _checked = True
        if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
            try:
                from langfuse import get_client
            except ImportError:
                print("  [observe] Langfuse keys set but the 'langfuse' extra is not installed; tracing off",
                      file=sys.stderr, flush=True)
            else:
                _client = get_client()
    return _client


def use(fake) -> None:
    """Install a client explicitly (tests use a fake; None turns tracing off)."""
    global _client, _checked
    _client, _checked = fake, True


def reset() -> None:
    """Forget the client, so the next call re-reads the environment."""
    global _client, _checked
    _client, _checked = None, False


@contextlib.contextmanager
def observation(name: str, as_type: str = "span", **fields):
    """A Langfuse observation around a block, or nothing when tracing is off. Yields the observation or None."""
    c = client()
    if c is None:
        yield None
        return
    with c.start_as_current_observation(name=name, as_type=as_type, **fields) as obs:
        yield obs


def update(obs, **fields) -> None:
    if obs is not None:
        obs.update(**fields)


def _summary(out: dict) -> dict:
    """Node output for the trace; the draft is shown by the writer's generation, so only its length here."""
    return {k: v for k, v in out.items() if k != "draft"} | ({"draft_chars": len(out["draft"])} if "draft" in out else {})


def node(name: str, fn, as_type: str = "agent"):
    """Wrap an async LangGraph node so that each execution is one observation."""
    @functools.wraps(fn)
    async def wrapped(state):
        with observation(name, as_type=as_type) as obs:
            out = await fn(state)
            update(obs, output=_summary(out))
            return out
    return wrapped


def flush() -> None:
    c = client()
    if c is not None:
        c.flush()

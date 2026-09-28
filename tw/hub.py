"""Connect an agent to the project's MCP servers and expose their tools as function-calling tools.

Servers run in-process through the MCP SDK's in-memory transport: real MCP sessions (initialize,
tools/list, tools/call), without subprocesses. Each agent gets only the servers it is allowed to use; the
tool name seen by the model is "<server>__<tool>".

Tool pinning (M3 defence): the name, description and input schema of every legitimate tool are hashed into
data/tool_pins.json at a trusted time (`python -m tw.hub pin`). At connection, a tool whose hash differs, or
that is not pinned, is not given to the model and is reported in `Hub.rejected`: a poisoned tool description
never reaches the prompt.
"""
from __future__ import annotations

import hashlib
import json
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field

from mcp.client.session import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from tw import observe
from tw.config import DATA

MAX_RESULT_CHARS = 8000
PINS = DATA / "tool_pins.json"
_DEFAULT = object()


def all_servers() -> dict[str, FastMCP]:
    from tw.mcp_servers import compliance, firm_kb, outbox, tenders

    return {"tenders": tenders.mcp, "firm_kb": firm_kb.mcp, "compliance": compliance.mcp, "outbox": outbox.mcp}


def tool_hash(name: str, description: str, schema: dict) -> str:
    # Whitespace is normalised: Python 3.13+ strips docstring indentation, 3.12 does not, and the same code must
    # give the same pin on every interpreter.
    description = " ".join(description.split())
    return hashlib.sha256(json.dumps([name, description, schema], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_pins() -> dict[str, str] | None:
    return json.loads(PINS.read_text(encoding="utf-8")) if PINS.exists() else None


@dataclass
class ToolCall:
    name: str
    arguments: dict
    ok: bool
    chars: int


@dataclass
class Hub:
    sessions: dict[str, ClientSession]
    pins: dict[str, str] | None = None
    tools: list[dict] = field(default_factory=list)      # function-calling schemas
    log: list[ToolCall] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)     # tools refused by pinning

    async def load(self) -> None:
        for server, session in self.sessions.items():
            for t in (await session.list_tools()).tools:
                name, description = f"{server}__{t.name}", (t.description or "")[:1000]
                schema = t.inputSchema or {"type": "object", "properties": {}}
                if self.pins is not None and self.pins.get(name) != tool_hash(name, description, schema):
                    self.rejected.append(name)
                    print(f"  [hub] tool {name} does not match its pin: not exposed", file=sys.stderr, flush=True)
                    continue
                self.tools.append({"type": "function", "function": {"name": name, "description": description,
                                                                     "parameters": schema}})

    async def call(self, name: str, arguments: dict) -> str:
        with observe.observation(name, as_type="tool", input=arguments) as obs:
            text = await self._call(name, arguments)
            ok = self.log[-1].ok
            observe.update(obs, output=text[:observe.MAX_TOOL_OUTPUT], metadata={"ok": ok, "chars": len(text)},
                           level=None if ok else "WARNING")
            return text

    async def _call(self, name: str, arguments: dict) -> str:
        server, _, tool = name.partition("__")
        session = self.sessions.get(server)
        allowed = {t["function"]["name"] for t in self.tools}
        if session is None or not tool or name not in allowed:  # rejected or unknown tools cannot be called either
            self.log.append(ToolCall(name, arguments, False, 0))
            return json.dumps({"error": f"unknown tool {name!r}"})
        try:
            res = await session.call_tool(tool, arguments)
        except Exception as exc:  # a bad call must not crash the run; the model sees the error
            self.log.append(ToolCall(name, arguments, False, 0))
            return json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        if res.structuredContent is not None:
            text = json.dumps(res.structuredContent, ensure_ascii=False)
        else:
            text = "\n".join(getattr(c, "text", "") for c in res.content)
        self.log.append(ToolCall(name, arguments, not res.isError, len(text)))
        return text[:MAX_RESULT_CHARS] + ("…[truncated]" if len(text) > MAX_RESULT_CHARS else "")


@asynccontextmanager
async def connect(servers: dict[str, FastMCP], pins=_DEFAULT):
    """pins: a {tool name: hash} manifest, None to disable pinning, or by default data/tool_pins.json if present."""
    async with AsyncExitStack() as stack:
        sessions = {name: await stack.enter_async_context(create_connected_server_and_client_session(srv))
                    for name, srv in servers.items()}
        hub = Hub(sessions, pins=load_pins() if pins is _DEFAULT else pins)
        await hub.load()
        yield hub


async def _pin() -> dict[str, str]:
    async with connect(all_servers(), pins=None) as hub:
        return {t["function"]["name"]: tool_hash(t["function"]["name"], t["function"]["description"],
                                                  t["function"]["parameters"]) for t in hub.tools}


if __name__ == "__main__":
    import asyncio

    if sys.argv[1:] == ["pin"]:
        pins = asyncio.run(_pin())
        PINS.write_text(json.dumps(pins, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"pinned {len(pins)} tools -> {PINS}")

"""The read-only MCP servers over Streamable HTTP, for third-party MCP clients such as Dify.

    MCP_HTTP_TOKEN=<random, >= 32 chars> python -m tw.mcp_servers.http
    -> http://127.0.0.1:8765/firm_kb/mcp   /tenders/mcp   /compliance/mcp

Every request needs `Authorization: Bearer <MCP_HTTP_TOKEN>`; the server refuses to start without a token.
`outbox` is deliberately NOT exposed: the only write action stays local, behind the human approval token.
Behind a tunnel or reverse proxy, list the public host name in MCP_ALLOWED_HOSTS (comma-separated), otherwise
the SDK's DNS-rebinding protection rejects the request (HTTP 421).
"""
from __future__ import annotations

import contextlib
import hmac
import os

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

from tw.mcp_servers import compliance, firm_kb, tenders

SERVERS = {"firm_kb": firm_kb.mcp, "tenders": tenders.mcp, "compliance": compliance.mcp}
MIN_TOKEN_LEN = 32


class BearerAuth:
    """Pure ASGI middleware: constant-time bearer check before anything reaches an MCP server."""

    def __init__(self, app, token: str):
        self.app, self._expected = app, f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            got = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(got, self._expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def build_app(token: str, allowed_hosts: list[str] | None = None, port: int = 8765):
    if len(token) < MIN_TOKEN_LEN:
        raise SystemExit(f"MCP_HTTP_TOKEN must be at least {MIN_TOKEN_LEN} characters")
    hosts = [f"127.0.0.1:{port}", f"localhost:{port}", *(allowed_hosts or [])]
    for server in SERVERS.values():
        server.settings.stateless_http = True  # no session affinity needed behind a proxy
        server.settings.transport_security = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=[])

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with contextlib.AsyncExitStack() as stack:
            for server in SERVERS.values():
                await stack.enter_async_context(server.session_manager.run())
            yield

    routes = [Mount(f"/{name}", app=server.streamable_http_app()) for name, server in SERVERS.items()]
    return BearerAuth(Starlette(routes=routes, lifespan=lifespan), token)


def main() -> None:
    import uvicorn

    port = int(os.environ.get("MCP_HTTP_PORT", "8765"))
    hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    app = build_app(os.environ.get("MCP_HTTP_TOKEN", ""), hosts, port)
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()

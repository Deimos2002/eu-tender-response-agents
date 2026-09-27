import pytest
from starlette.testclient import TestClient

from tw.mcp_servers import http

TOKEN = "t" * 40


def test_refuses_to_start_without_a_strong_token():
    with pytest.raises(SystemExit):
        http.build_app("")
    with pytest.raises(SystemExit):
        http.build_app("short-token")


def test_write_server_is_never_exposed():
    assert "outbox" not in http.SERVERS
    assert all(s.name != "outbox" for s in http.SERVERS.values())


def test_every_path_needs_the_bearer_token():
    client = TestClient(http.build_app(TOKEN))  # no lifespan: the auth check runs before any MCP server
    for path in ("/firm_kb/mcp", "/tenders/mcp", "/compliance/mcp", "/outbox/mcp", "/"):
        assert client.post(path, json={}).status_code == 401
        assert client.post(path, json={}, headers={"Authorization": "Bearer " + "x" * 40}).status_code == 401


def test_public_host_must_be_allowed():
    http.build_app(TOKEN, ["mcp.example.org"])
    hosts = http.SERVERS["firm_kb"].settings.transport_security.allowed_hosts
    assert "mcp.example.org" in hosts and "127.0.0.1:8765" in hosts

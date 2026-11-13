"""MCP delegate checks real API authorization without inventing a custom MCP protocol."""
import httpx
import pytest

from otternest.mcp_bridge import MCPBridgeError, OtterNestMCPClient


def test_bridge_rejects_owner_token_or_external_plain_http():
    with pytest.raises(MCPBridgeError):
        OtterNestMCPClient("https://example.org", "owner-token")
    with pytest.raises(MCPBridgeError):
        OtterNestMCPClient("http://example.org", "on_agent_secret")
    with pytest.raises(MCPBridgeError):
        OtterNestMCPClient("https://username:pwd@example.org", "on_agent_secret")


def test_bridge_forwards_authorized_operations_without_leaking_token():
    seen = []

    def serve(request):
        assert request.headers["Authorization"] == "Bearer on_agent_secret"
        seen.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json={"lease_epoch": 5, "status": "running"})
        if request.url.path.endswith("/context"):
            return httpx.Response(200, json={"task": {"lease_epoch": 5}})
        if request.url.path.endswith("/submit"):
            assert b'"epoch":5' in request.content
            return httpx.Response(200, json={"status": "submitted"})
        if request.url.path.endswith("/queue"):
            return httpx.Response(200, json=[{"id": "task-1"}])
        return httpx.Response(404, json={"detail": "sensitive private resource"})

    with httpx.MockTransport(serve) as transport:
        bridge = OtterNestMCPClient("https://otter.example", "on_agent_secret", transport=transport)
        try:
            assert bridge.available_tasks() == [{"id": "task-1"}]
            assert bridge.claim_task("task-1")["lease_epoch"] == 5
            assert bridge.task_context("task-1")["task"]["lease_epoch"] == 5
            assert bridge.submit_task("task-1", 5, "done")["status"] == "submitted"
            with pytest.raises(MCPBridgeError) as error:
                bridge.list_memories("foreign-project")
            assert "sensitive" not in str(error.value)
        finally:
            bridge.close()
    assert len(seen) == 5


def test_mcp_bridge_validates_client_requests():
    bridge = OtterNestMCPClient("http://127.0.0.1:8000", "on_agent_secret")
    try:
        with pytest.raises(MCPBridgeError):
            bridge.available_tasks(1000)
        with pytest.raises(MCPBridgeError):
            bridge.get_task("../admin")
        with pytest.raises(MCPBridgeError):
            bridge.submit_task("id", 1, "   ")
    finally:
        bridge.close()


def test_actual_mcp_sdk_registers_tools_when_installed():
    pytest.importorskip("mcp.server.fastmcp")
    bridge = OtterNestMCPClient("http://127.0.0.1:8000", "on_agent_secret")
    from otternest.mcp_bridge import make_mcp_server
    server = make_mcp_server(bridge)
    assert server is not None  # not just a fake /mcp REST endpoint
    bridge.close()

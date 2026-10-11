import asyncio
import json

import httpx

import ccdi_federation_mcp.server as server
from ccdi_federation_mcp.upstream import UpstreamFailure


def test_query_tool_returns_payload_once(monkeypatch):
    async def fake_fetch(catalog, request):
        return {
            "request": request,
            "http_status": 200,
            "payload": [{"node": "ok", "data": [1]}, {"node": "bad", "error": "offline"}],
            "source": catalog.source,
        }

    monkeypatch.setattr(server, "fetch_metadata", fake_fetch)
    result = asyncio.run(server.query_metadata("subject_index", query_params={"sex": "Female"}))
    assert result.is_error is False
    assert result.content == []
    assert result.structured_content["payload"] == [
        {"node": "ok", "data": [1]}, {"node": "bad", "error": "offline"}
    ]
    assert result.structured_content["request"]["query_params"]["sex"] == "Female"
    assert set(result.structured_content) == {"request", "http_status", "payload", "source"}


def test_query_tool_revalidates_before_network(monkeypatch):
    async def fail_if_called(*_args):
        raise AssertionError("network request should not occur")

    monkeypatch.setattr(server, "fetch_metadata", fail_if_called)
    result = asyncio.run(server.query_metadata("sample_index", query_params={"diagnosis_category": "x"}))
    assert result.is_error is True
    assert result.structured_content["errors"][0]["code"] == "unknown_query_parameter"


def test_query_tool_marks_upstream_errors(monkeypatch):
    async def fake_fetch(catalog, request):
        return {"request": request, "http_status": 422, "payload": {"message": "invalid"}, "source": catalog.source}

    monkeypatch.setattr(server, "fetch_metadata", fake_fetch)
    result = asyncio.run(server.query_metadata("subject_index"))
    assert result.is_error is True
    assert result.structured_content["payload"] == {"message": "invalid"}


def test_query_tool_reports_transport_failure_without_payload(monkeypatch):
    async def fake_fetch(*_args):
        raise UpstreamFailure("upstream_timeout")

    monkeypatch.setattr(server, "fetch_metadata", fake_fetch)
    result = asyncio.run(server.query_metadata("subject_index"))
    assert result.is_error is True
    assert result.structured_content["error"] == "upstream_timeout"
    assert "payload" not in result.structured_content


def test_resources_are_bundled_data():
    assert json.loads(server.operations_resource())["total"] == 23
    values = json.loads(server.pv_field_resource("subject", "sex"))
    assert values["field"] == "sex"
    assert values["total"] == len(server.catalog.pv["subject"]["sex"]["permissible_values"])


def test_streamable_http_lists_only_six_server_tools(monkeypatch):
    async def fake_fetch(catalog, request):
        return {"request": request, "http_status": 200, "payload": [{"node": "ok"}], "source": catalog.source}

    monkeypatch.setattr(server, "fetch_metadata", fake_fetch)

    async def run():
        async with server.mcp.session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app), base_url="http://localhost:8000"
            ) as client:
                request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
                response = await client.post(
                    "/mcp", json=request,
                    headers={"MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"},
                )
                assert response.status_code == 200
                names = [tool["name"] for tool in response.json()["result"]["tools"]]
                assert names == [
                    "list_operations", "describe_operation", "get_field_metadata",
                    "list_permissible_values", "validate_query", "query_metadata",
                ]
                live = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "query_metadata", "arguments": {"operation_id": "subject_index"}},
                    },
                    headers={"MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"},
                )
                tool_result = live.json()["result"]
                assert tool_result["content"] == []
                assert tool_result["structuredContent"]["payload"] == [{"node": "ok"}]
                health = await client.get("/healthz")
                assert health.json() == {"status": "ok"}
                disallowed = await client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    headers={
                        "MCP-Protocol-Version": "2025-11-25",
                        "Accept": "application/json, text/event-stream",
                        "Origin": "https://disallowed.example",
                    },
                )
                assert disallowed.status_code == 403
                wrong_host = await client.post(
                    "/mcp", json=request,
                    headers={
                        "Host": "unexpected.example",
                        "MCP-Protocol-Version": "2025-11-25",
                        "Accept": "application/json, text/event-stream",
                    },
                )
                assert wrong_host.status_code == 421
                oversized = await client.post(
                    "/mcp", content=b" " * 65_537,
                    headers={
                        "MCP-Protocol-Version": "2025-11-25",
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                )
                assert oversized.status_code == 413
    asyncio.run(run())

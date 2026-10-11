import httpx
import pytest

import popsci_mcp.server as server


@pytest.mark.asyncio
async def test_mcp_discovery_and_offline_pv_lookup():
    tools = await server.mcp.list_tools()
    names = {tool.name for tool in tools}
    assert {
        "list_query_fields", "describe_query_field", "describe_type", "list_properties",
        "describe_property", "list_permissible_values", "check_permissible_value",
        "validate_graphql", "run_graphql_query", "get_study_overview", "list_studies",
        "list_study_files", "get_study_demographics",
    } <= names
    assert server.describe_query_field("tabStudy")["enabled_for_live_mcp"]
    assert not server.describe_query_field("study_subject")["enabled_for_live_mcp"]
    assert server.check_permissible_value("study_status", "Closed")["listed"]


@pytest.mark.asyncio
async def test_curated_queries_omit_unused_filters(monkeypatch):
    calls = []

    async def fake_fetch(query, variables=None):
        calls.append((query, variables))
        return {"http_status": 200, "endpoint": "test", "payload": {"data": {}}}

    monkeypatch.setattr(server, "fetch_graphql", fake_fetch)
    assert not (await server.get_study_overview()).is_error
    assert not (await server.list_studies(first=1)).is_error
    assert "study_design:" not in calls[-1][0]
    assert not (await server.list_studies(first=1, study_design=["Clinical Trial"])).is_error
    assert calls[-1][1]["study_design"] == ["Clinical Trial"]
    assert not (await server.list_study_files(first=1)).is_error
    assert "study_short_name:" not in calls[-1][0]
    assert not (await server.get_study_demographics(["NLST"], first=1)).is_error
    assert (await server.list_studies(first=101)).is_error
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_http_health_discovery_and_origin_rejection():
    async with server.mcp.session_manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost:8003") as client:
            health = await client.get("/healthz")
            assert health.status_code == 200
            headers = {"MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"}
            discovery = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers,
            )
            assert discovery.status_code == 200
            assert any(tool["name"] == "run_graphql_query" for tool in discovery.json()["result"]["tools"])
            lookup = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                    "name": "check_permissible_value",
                    "arguments": {"property_name": "study_status", "value": "Closed"},
                }}, headers=headers,
            )
            assert lookup.json()["result"]["structuredContent"]["listed"] is True
            rejected = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
                headers={**headers, "Origin": "https://untrusted.example"},
            )
            assert rejected.status_code == 403

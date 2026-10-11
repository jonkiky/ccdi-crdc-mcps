import httpx
import pytest

import ccdi_dcc_mcp.server as server


@pytest.mark.asyncio
async def test_mcp_discovery_and_offline_tools():
    tools = await server.mcp.list_tools()
    names = {tool.name for tool in tools}
    assert {
        "list_query_fields", "describe_query_field", "describe_type", "list_properties",
        "describe_property", "list_permissible_values", "check_permissible_value",
        "validate_graphql", "run_graphql_query", "list_studies",
        "search_participants", "count_entities",
    } <= names
    assert server.describe_query_field("studies")["enabled_for_live_mcp"]
    assert not server.describe_query_field("sampleOfSampleParticipantEdges")["enabled_for_live_mcp"]
    assert server.list_permissible_values("sex_at_birth")["values"][0] == "Female"


@pytest.mark.asyncio
async def test_curated_tools_validate_before_fetch(monkeypatch):
    calls = []

    async def fake_fetch(query, variables=None):
        calls.append((query, variables))
        return {"http_status": 200, "endpoint": "test", "payload": {"data": {}}}

    monkeypatch.setattr(server, "fetch_graphql", fake_fetch)
    assert not (await server.list_studies(limit=2)).is_error
    assert calls[0][1] == {"limit": 2, "offset": 0}
    assert (await server.list_studies(limit=101)).is_error
    assert (await server.search_participants(where={"wrong": "x"})).is_error
    assert not (await server.search_participants(where={"sex_at_birth_EQ": "Female"})).is_error
    assert not (await server.count_entities("studies")).is_error
    assert (await server.count_entities("sampleOfSampleParticipantEdges")).is_error
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_http_health_discovery_and_origin_rejection():
    async with server.mcp.session_manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost:8002") as client:
            health = await client.get("/healthz")
            assert health.status_code == 200
            headers = {"MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"}
            discovery = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers,
            )
            assert discovery.status_code == 200
            assert any(tool["name"] == "run_graphql_query" for tool in discovery.json()["result"]["tools"])
            pv = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                    "name": "check_permissible_value",
                    "arguments": {"property_name": "sex_at_birth", "value": "Female"},
                }}, headers=headers,
            )
            assert pv.json()["result"]["structuredContent"]["listed"] is True
            rejected = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
                headers={**headers, "Origin": "https://untrusted.example"},
            )
            assert rejected.status_code == 403

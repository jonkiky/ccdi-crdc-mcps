import httpx
import pytest

import c3dc_mcp.server as server


@pytest.mark.asyncio
async def test_mcp_tool_discovery_and_offline_pv_lookup():
    tools = await server.mcp.list_tools()
    names = {tool.name for tool in tools}
    assert {
        "list_query_fields", "describe_query_field", "describe_type", "list_properties",
        "describe_property", "list_permissible_values", "check_permissible_value",
        "validate_graphql", "run_graphql_query", "get_overview_counts",
        "list_studies", "search_participants",
    } <= names
    assert all("model" not in tool.input_schema.get("properties", {}) for tool in tools)
    assert server.list_permissible_values("sex_at_birth")["values"][0] == "Female"
    assert server.describe_query_field("searchParticipants")["enabled_for_live_mcp"]
    assert any(field["name"] == "numberOfParticipants" for field in server.describe_type("SearchParticipantsResult")["fields"])


@pytest.mark.asyncio
async def test_curated_tools_use_bounded_queries(monkeypatch):
    calls = []

    async def fake_fetch(query, variables=None):
        calls.append((query, variables))
        return {"http_status": 200, "endpoint": "test", "payload": {"data": {}}}

    monkeypatch.setattr(server, "fetch_graphql", fake_fetch)
    result = await server.list_studies(first=2)
    assert not result.is_error
    assert calls[0][1] == {"first": 2, "offset": 0}
    rejected = await server.list_studies(first=101)
    assert rejected.is_error
    assert len(calls) == 1
    filtered = await server.search_participants({"sex_at_birth": ["Female"]})
    assert not filtered.is_error
    assert calls[1][1] == {"sex_at_birth": ["Female"]}
    invalid = await server.search_participants({"not_a_filter": ["x"]})
    assert invalid.is_error
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_http_health_and_origin_rejection():
    async with server.mcp.session_manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://localhost:8001") as client:
            health = await client.get("/healthz")
            assert health.status_code == 200
            headers = {"MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"}
            discovery = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers,
            )
            assert discovery.status_code == 200
            assert any(tool["name"] == "run_graphql_query" for tool in discovery.json()["result"]["tools"])
            lookup = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                    "name": "check_permissible_value",
                    "arguments": {"property_name": "sex_at_birth", "value": "Female"},
                }},
                headers=headers,
            )
            assert lookup.status_code == 200
            assert lookup.json()["result"]["structuredContent"]["listed"] is True
            response = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Origin": "https://untrusted.example", "MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"},
            )
            assert response.status_code == 403

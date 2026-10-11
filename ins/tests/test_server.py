import httpx
import pytest

import ins_mcp.server as server


@pytest.mark.asyncio
async def test_mcp_discovery_has_ins_tools_without_pv_tools():
    tools = await server.mcp.list_tools()
    names = {tool.name for tool in tools}
    assert {
        "list_query_fields", "describe_query_field", "describe_type",
        "validate_graphql", "run_graphql_query", "get_catalog_counts",
        "list_programs", "list_projects", "search_projects",
        "get_project_details", "get_dataset_details",
    } <= names
    assert "list_permissible_values" not in names
    assert "check_permissible_value" not in names
    assert server.describe_query_field("projectsOverview")["enabled_for_live_mcp"]
    assert not server.describe_query_field("projectsInList")["enabled_for_live_mcp"]


@pytest.mark.asyncio
async def test_curated_queries_apply_limits_and_send_operation_name(monkeypatch):
    calls = []

    async def fake_fetch(query, variables, operation_name):
        calls.append((query, variables, operation_name))
        return {"http_status": 200, "endpoint": "test", "payload": {"data": {}}}

    monkeypatch.setattr(server, "fetch_graphql", fake_fetch)
    counts = await server.get_catalog_counts()
    assert not counts.is_error
    assert calls[0][2] == "CatalogCounts"
    projects = await server.list_projects(first=2, program_ids=["program-1"])
    assert not projects.is_error
    assert calls[1][1] == {"first": 2, "offset": 0, "program_ids": ["program-1"]}
    rejected = await server.list_projects(first=101)
    assert rejected.is_error
    assert len(calls) == 2
    search = await server.search_projects()
    assert not search.is_error
    assert calls[2][1] == {
        "program_ids": [], "program_names": [], "focus_area": [], "cancer_type": [],
    }
    details = await server.get_project_details("project-1")
    assert not details.is_error
    assert calls[3][2] == "ProjectDetails"
    missing = await server.get_project_details("")
    assert missing.is_error
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_http_health_discovery_and_origin_rejection():
    async with server.mcp.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://localhost:8002",
        ) as client:
            health = await client.get("/healthz")
            assert health.status_code == 200
            headers = {"MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"}
            discovery = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers,
            )
            assert discovery.status_code == 200
            assert any(tool["name"] == "run_graphql_query" for tool in discovery.json()["result"]["tools"])
            response = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={**headers, "Origin": "https://untrusted.example"},
            )
            assert response.status_code == 403

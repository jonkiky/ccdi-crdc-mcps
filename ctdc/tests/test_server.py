import httpx
import pytest

import ctdc_mcp.server as server


@pytest.mark.asyncio
async def test_mcp_tools_have_no_pv_lookup():
    tools = await server.mcp.list_tools()
    names = {tool.name for tool in tools}
    assert {
        "list_query_fields", "describe_query_field", "describe_type",
        "validate_graphql", "run_graphql_query", "get_home_counts",
        "list_studies", "get_study_details", "get_study_diagnoses",
        "get_study_specimen_summary", "get_study_clinical_counts",
        "get_study_trial_counts", "search_participants",
        "list_participants", "list_files",
    } <= names
    assert "list_permissible_values" not in names
    assert "check_permissible_value" not in names
    assert server.describe_query_field("participantOverview")["enabled_for_live_mcp"]
    assert not server.describe_query_field("biospecimenOverview")["enabled_for_live_mcp"]
    assert not server.describe_query_field("fileIDsFromList")["enabled_for_live_mcp"]


@pytest.mark.asyncio
async def test_curated_queries_are_bounded(monkeypatch):
    calls = []

    async def fake_fetch(query, variables, operation_name):
        calls.append((query, variables, operation_name))
        return {"http_status": 200, "endpoint": "test", "payload": {"data": {}}}

    monkeypatch.setattr(server, "fetch_graphql", fake_fetch)
    counts = await server.get_home_counts()
    assert not counts.is_error
    assert calls[0][2] == "HomeCounts"
    participants = await server.list_participants(first=2, study_short_name=["CMB"])
    assert not participants.is_error
    assert calls[1][1] == {"first": 2, "offset": 0, "study_short_name": ["CMB"]}
    rejected = await server.list_participants(first=101)
    assert rejected.is_error
    assert len(calls) == 2
    details = await server.get_study_details("CMB")
    assert not details.is_error
    assert calls[2][1] == {"study_short_name": ["CMB"]}
    missing = await server.get_study_details("")
    assert missing.is_error
    assert len(calls) == 3
    filtered = await server.search_participants({"study_short_name": ["CMB"]})
    assert not filtered.is_error
    assert calls[3][1] == {"study_short_name": ["CMB"]}
    invalid = await server.search_participants({"not_a_filter": ["x"]})
    assert invalid.is_error
    assert len(calls) == 4
    for tool in (
        server.get_study_diagnoses,
        server.get_study_specimen_summary,
        server.get_study_clinical_counts,
        server.get_study_trial_counts,
    ):
        result = await tool("CMB")
        assert not result.is_error
        assert calls[-1][1] == {"study_short_name": ["CMB"]}
    assert len(calls) == 8


@pytest.mark.asyncio
async def test_http_health_discovery_and_origin_rejection():
    async with server.mcp.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://localhost:8003",
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

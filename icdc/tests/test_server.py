"""MCP tool surface and ASGI behavior without a live upstream dependency."""

import json

import httpx
import pytest

import icdc_mcp.server as server
from icdc_mcp.query import UpstreamFailure


@pytest.mark.asyncio
async def test_tools_expose_icdc_metadata_without_pv_lookup():
    tools = await server.mcp.list_tools()
    names = {tool.name for tool in tools}
    assert {
        "list_query_fields", "describe_query_field", "describe_type",
        "validate_graphql", "run_graphql_query", "get_overview_counts",
        "list_studies", "list_cases", "search_icdc",
    } <= names
    assert all("permissible" not in name.lower() and "_pv" not in name.lower() for name in names)
    assert server.describe_query_field("studiesByProgram")["enabled_for_live_mcp"]
    assert not server.describe_query_field("createManifest")["enabled_for_live_mcp"]
    assert any(
        field["name"] == "createManifest" and not field["enabled_for_live_mcp"]
        for field in server.list_query_fields(offset=0, limit=100)["fields"]
    )


@pytest.mark.asyncio
async def test_convenience_tools_validate_before_one_upstream_call(monkeypatch):
    calls = []

    async def fake_fetch(query, variables, operation_name):
        calls.append((query, variables, operation_name))
        return {"http_status": 200, "endpoint": "test", "payload": {"data": {}}}

    monkeypatch.setattr(server, "fetch_graphql", fake_fetch)
    for call in (
        server.get_overview_counts(),
        server.list_studies(first=2, offset=1),
        server.list_cases(first=3),
        server.search_icdc("glioma", first=4, offset=2),
    ):
        assert not (await call).is_error
    assert [call[2] for call in calls] == ["OverviewCounts", "Studies", "Cases", "SearchICDC"]
    assert calls[1][1] == {"first": 2, "offset": 1}
    assert calls[2][1] == {"first": 3, "offset": 0}
    assert calls[3][1] == {"term": "glioma", "first": 4, "offset": 2}

    assert (await server.list_studies(first=101)).is_error
    assert (await server.list_cases(offset=-1)).is_error
    assert (await server.search_icdc(" ")).is_error
    assert (await server.run_graphql_query("query Bad { createManifest }")).is_error
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_upstream_partial_errors_and_failures_are_returned(monkeypatch):
    async def partial(_query, _variables, _operation_name):
        return {
            "http_status": 200,
            "endpoint": "test",
            "payload": {"data": {"numberOfStudies": 21}, "errors": [{"message": "partial"}]},
        }

    monkeypatch.setattr(server, "fetch_graphql", partial)
    result = await server.run_graphql_query("query Counts { numberOfStudies }")
    assert result.is_error
    assert result.structured_content["payload"]["data"] == {"numberOfStudies": 21}
    assert result.structured_content["payload"]["errors"] == [{"message": "partial"}]
    assert result.structured_content["source"] == server.catalog.source()
    assert json.loads(result.content[0].text) == result.structured_content

    async def timeout(_query, _variables, _operation_name):
        raise UpstreamFailure("upstream_timeout")

    monkeypatch.setattr(server, "fetch_graphql", timeout)
    failed = await server.get_overview_counts()
    assert failed.is_error
    assert failed.structured_content["error"] == "upstream_timeout"
    assert failed.structured_content["source"] == server.catalog.source()
    assert json.loads(failed.content[0].text) == failed.structured_content


@pytest.mark.asyncio
async def test_streamable_http_health_discovery_and_origin_rejection():
    async with server.mcp.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://localhost:8004",
        ) as client:
            health = await client.get("/healthz")
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}
            ready = await client.get("/readyz")
            assert ready.status_code == 200
            assert ready.json()["schema_sha256"] == server.catalog.schema_sha256
            headers = {
                "MCP-Protocol-Version": "2025-11-25",
                "Accept": "application/json, text/event-stream",
            }
            discovery = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers,
            )
            assert discovery.status_code == 200
            listed = {tool["name"] for tool in discovery.json()["result"]["tools"]}
            assert {"run_graphql_query", "search_icdc"} <= listed
            checked = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {
                        "name": "validate_graphql",
                        "arguments": {"query": "query Counts { numberOfStudies }"},
                    },
                },
                headers=headers,
            )
            assert checked.status_code == 200
            assert checked.json()["result"]["structuredContent"]["valid"]
            rejected = await client.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
                headers={**headers, "Origin": "https://untrusted.example"},
            )
            assert rejected.status_code == 403

import asyncio
import json

import httpx

import ccdc_mcp.server as server
from ccdc_mcp.upstream import API_ORIGIN, UpstreamFailure, fetch_json


def test_upstream_uses_fixed_origin_and_read_only_search_body():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "success", "data": {"result": [], "pageInfo": {}}})

    async def run():
        return await fetch_json(
            "POST",
            "/service/dataresources/search",
            body={"facet_filters": {"resource_type": ["Registry"]}, "pageInfo": {"page": 1, "pageSize": 10}},
            transport=httpx.MockTransport(handler),
        )

    status, payload = asyncio.run(run())
    assert status == 200
    assert payload["status"] == "success"
    assert str(requests[0].url) == API_ORIGIN + "/service/dataresources/search"
    assert requests[0].method == "POST"
    assert json.loads(requests[0].content)["facet_filters"]["resource_type"] == ["Registry"]


def test_upstream_rejects_redirect():
    async def run():
        transport = httpx.MockTransport(lambda _: httpx.Response(302, headers={"Location": "https://other.example"}))
        try:
            await fetch_json("GET", "/service/dataresources/filters", transport=transport)
        except UpstreamFailure as exc:
            return exc
        raise AssertionError("redirect should fail")

    error = asyncio.run(run())
    assert error.code == "upstream_redirect_rejected"
    assert error.status == 302


def test_tools_validate_inputs_and_keep_resource_id_in_one_path_segment(monkeypatch):
    calls = []

    async def fake_fetch(method, path, *, body=None):
        calls.append((method, path, body))
        return 200, {"status": "success", "data": {"data_resource_id": "All of Us"}}

    monkeypatch.setattr(server, "fetch_json", fake_fetch)

    async def run():
        invalid = await server.get_resource("../secret")
        detail = await server.get_resource("All of Us")
        invalid_page = await server.search_resources(page_size=1000)
        return invalid, detail, invalid_page

    invalid, detail, invalid_page = asyncio.run(run())
    assert invalid.is_error and invalid_page.is_error
    assert detail.structured_content["resource"]["data_resource_id"] == "All of Us"
    assert calls == [("GET", "/service/dataresources/All%20of%20Us", None)]


def test_search_and_datasets_follow_observed_api_shapes(monkeypatch):
    calls = []

    async def fake_fetch(method, path, *, body=None):
        calls.append((method, path, body))
        if path.endswith("/search"):
            return 200, {"status": "success", "data": {
                "pageInfo": {"page": 2, "pageSize": 1, "total": 3},
                "result": [{"data_resource_id": "AFCRN", "resource_name": "African Cancer Registry Network"}],
            }}
        return 200, {"status": "success", "data": [
            {"dataset_id": "one"}, {"dataset_id": "two"}, {"dataset_id": "three"}
        ]}

    monkeypatch.setattr(server, "fetch_json", fake_fetch)

    async def run():
        return (
            await server.search_resources(resource_types=["Registry"], page=2, page_size=1),
            await server.list_resource_datasets("AFCRN", offset=1, limit=1),
        )

    search, datasets = asyncio.run(run())
    assert search.structured_content["page_info"]["total"] == 3
    assert search.structured_content["resources"][0]["data_resource_id"] == "AFCRN"
    assert calls[0][2] == {
        "facet_filters": {"resource_type": ["Registry"], "data_content_type": []},
        "pageInfo": {"page": 2, "pageSize": 1},
    }
    assert datasets.structured_content["datasets"] == [{"dataset_id": "two"}]
    assert datasets.structured_content["next_offset"] == 2
    assert datasets.structured_content["total"] == 3


def test_missing_resource_matches_live_api_response(monkeypatch):
    async def fake_fetch(method, path, *, body=None):
        return 200, {"status": "success"}

    monkeypatch.setattr(server, "fetch_json", fake_fetch)
    result = asyncio.run(server.get_resource("missing"))
    assert result.is_error
    assert result.structured_content["error"] == "resource_not_found"


def test_streamable_http_exposes_tools_resource_and_host_protection(monkeypatch):
    async def fake_fetch(method, path, *, body=None):
        assert method == "GET" and path.endswith("/filters")
        return 200, {"status": "success", "data": {"searchFilters": {"Resource Type": [{"name": "Registry", "count": 60}]}}}

    monkeypatch.setattr(server, "fetch_json", fake_fetch)

    async def run():
        async with server.mcp.session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app), base_url="http://localhost:8000"
            ) as client:
                headers = {"MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"}
                listing = await client.post(
                    "/mcp", headers=headers,
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                )
                names = [item["name"] for item in listing.json()["result"]["tools"]]
                assert names == ["list_filter_options", "search_resources", "get_resource", "list_resource_datasets"]
                tool = await client.post(
                    "/mcp", headers=headers,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                        "name": "list_filter_options", "arguments": {},
                    }},
                )
                assert tool.json()["result"]["structuredContent"]["filters"]["Resource Type"][0]["name"] == "Registry"
                resources = await client.post(
                    "/mcp", headers=headers,
                    json={"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}},
                )
                assert resources.json()["result"]["resources"][0]["uri"] == "ccdc://reference/catalog"
                content = await client.post(
                    "/mcp", headers=headers,
                    json={"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {
                        "uri": "ccdc://reference/catalog",
                    }},
                )
                assert "updated periodically" in content.json()["result"]["contents"][0]["text"]
                assert (await client.get("/healthz")).json() == {"status": "ok"}
                denied = await client.post(
                    "/mcp", headers={**headers, "Host": "unexpected.example"},
                    json={"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}},
                )
                assert denied.status_code == 421

    asyncio.run(run())

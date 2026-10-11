import asyncio
import json

import httpx

import cbioportal_mcp.server as server
from cbioportal_mcp.upstream import UpstreamFailure, fetch_api
from cbioportal_mcp.validation import validate_request


def test_upstream_get_and_post_use_fixed_origin_and_preserve_payload():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"items": [1, 2]})

    async def run():
        transport = httpx.MockTransport(handler)
        get = validate_request(server.catalog, "getAllStudiesUsingGET")["request"]
        post = validate_request(server.catalog, "fetchStudiesUsingPOST", body=["x"])["request"]
        return await fetch_api(server.catalog, get, transport=transport), await fetch_api(server.catalog, post, transport=transport)

    get_result, post_result = asyncio.run(run())
    assert get_result["payload"] == post_result["payload"] == {"items": [1, 2]}
    assert str(requests[0].url) == "https://cbioportal-api.ccdi.cancer.gov/api/studies?pageSize=20&pageNumber=0"
    assert requests[0].method == "GET"
    assert requests[1].method == "POST"
    assert json.loads(requests[1].content) == ["x"]


def test_upstream_rejects_redirect():
    async def run():
        request = validate_request(server.catalog, "getAllStudiesUsingGET")["request"]
        transport = httpx.MockTransport(lambda _: httpx.Response(302, headers={"Location": "https://example.com"}))
        try:
            await fetch_api(server.catalog, request, transport=transport)
        except UpstreamFailure as exc:
            return exc

    error = asyncio.run(run())
    assert error.code == "upstream_redirect_rejected"
    assert error.status == 302


def test_query_tool_validates_before_network(monkeypatch):
    async def fail_if_called(*_args):
        raise AssertionError("network should not be called")

    monkeypatch.setattr(server, "fetch_api", fail_if_called)
    result = asyncio.run(server.query_api("fetchStudiesUsingPOST"))
    assert result.is_error
    assert result.structured_content["errors"][0]["code"] == "missing_body"


def test_streamable_http_exposes_five_tools(monkeypatch):
    async def fake_fetch(catalog, request):
        return {"request": request, "http_status": 200, "payload": [{"studyId": "x"}], "source": catalog.source}

    monkeypatch.setattr(server, "fetch_api", fake_fetch)

    async def run():
        async with server.mcp.session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app), base_url="http://localhost:8000"
            ) as client:
                headers = {"MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"}
                listing = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, headers=headers)
                assert listing.status_code == 200
                names = [tool["name"] for tool in listing.json()["result"]["tools"]]
                assert names == ["list_operations", "describe_operation", "describe_schema", "validate_query", "query_api"]
                live = await client.post(
                    "/mcp", headers=headers,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                        "name": "query_api", "arguments": {"operation_id": "getAllStudiesUsingGET"},
                    }},
                )
                assert live.json()["result"]["structuredContent"]["payload"] == [{"studyId": "x"}]
                post = await client.post(
                    "/mcp", headers=headers,
                    json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                        "name": "query_api", "arguments": {
                            "operation_id": "fetchStudiesUsingPOST", "body": ["study_1"],
                        },
                    }},
                )
                assert post.json()["result"]["structuredContent"]["request"]["body"] == ["study_1"]
                assert (await client.get("/healthz")).json() == {"status": "ok"}
                assert (await client.get("/readyz")).json() == {"status": "ready"}
                wrong_host = await client.post(
                    "/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
                    headers={**headers, "Host": "unexpected.example"},
                )
                assert wrong_host.status_code == 421

    asyncio.run(run())

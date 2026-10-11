import asyncio
import json

import httpx
import pytest

from ccdi_federation_mcp.catalog import Catalog
from ccdi_federation_mcp import upstream
from ccdi_federation_mcp.validation import validate_query


@pytest.fixture(scope="module")
def catalog():
    return Catalog()


@pytest.fixture(scope="module")
def query_request(catalog):
    return validate_query(catalog, "subject_index", query_params={"sex": "Female"})["request"]


def test_one_page_full_payload_and_provenance(catalog, query_request):
    assert upstream.MAX_RESPONSE_BYTES == 24_000_000
    assert upstream.UPSTREAM_TIMEOUT_SECONDS == 600
    payload = [{"node": "ok", "results": [{"id": "x"}]}, {"node": "failed", "error": "offline"}]
    observed = []

    def handle(http_request):
        observed.append(http_request)
        return httpx.Response(200, json=payload)

    result = asyncio.run(upstream.fetch_metadata(catalog, query_request, transport=httpx.MockTransport(handle)))
    assert len(observed) == 1
    assert str(observed[0].url).startswith("https://federation.ccdi.cancer.gov/api/v1/subject?")
    assert observed[0].url.params["per_page"] == "20"
    assert observed[0].url.params["sex"] == "Female"
    assert result["payload"] == payload
    assert result["http_status"] == 200
    assert set(result) == {"request", "http_status", "payload", "source"}


def test_upstream_http_error_payload_is_preserved(catalog, query_request):
    transport = httpx.MockTransport(lambda _: httpx.Response(422, json={"message": "bad filter"}))
    result = asyncio.run(upstream.fetch_metadata(catalog, query_request, transport=transport))
    assert result["http_status"] == 422
    assert result["payload"] == {"message": "bad filter"}


def test_retry_once_for_429_and_5xx(catalog, query_request, monkeypatch):
    async def no_wait(_seconds):
        pass

    monkeypatch.setattr(upstream.asyncio, "sleep", no_wait)
    for first_status in (429, 500, 501, 599):
        calls = []

        def handle(_):
            calls.append(1)
            return httpx.Response(first_status if len(calls) == 1 else 200, json={"attempt": len(calls)})

        result = asyncio.run(upstream.fetch_metadata(catalog, query_request, transport=httpx.MockTransport(handle)))
        assert len(calls) == 2
        assert result["payload"] == {"attempt": 2}


@pytest.mark.parametrize("response,code", [
    (httpx.Response(302, headers={"Location": "https://example.com/"}), "upstream_redirect_rejected"),
    (httpx.Response(200, content=b"not JSON"), "upstream_invalid_json"),
])
def test_redirect_and_invalid_json(catalog, query_request, response, code):
    with pytest.raises(upstream.UpstreamFailure) as exc:
        asyncio.run(upstream.fetch_metadata(catalog, query_request, transport=httpx.MockTransport(lambda _: response)))
    assert exc.value.code == code


def test_decoded_response_limit_is_inclusive(catalog, query_request, monkeypatch):
    monkeypatch.setattr(upstream, "MAX_RESPONSE_BYTES", 21)
    accepted = httpx.MockTransport(lambda _: httpx.Response(200, content=json.dumps("x" * 19).encode()))
    assert asyncio.run(upstream.fetch_metadata(catalog, query_request, transport=accepted))["payload"] == "x" * 19
    rejected = httpx.MockTransport(lambda _: httpx.Response(200, content=json.dumps("x" * 20).encode()))
    with pytest.raises(upstream.UpstreamFailure) as exc:
        asyncio.run(upstream.fetch_metadata(catalog, query_request, transport=rejected))
    assert exc.value.code == "upstream_response_too_large"


def test_timeout_and_transport_failure(catalog, query_request):
    for error, code in (
        (httpx.ReadTimeout("late"), "upstream_timeout"),
        (httpx.ConnectError("unavailable"), "upstream_transport_error"),
    ):
        def handle(_):
            raise error

        with pytest.raises(upstream.UpstreamFailure) as exc:
            asyncio.run(upstream.fetch_metadata(catalog, query_request, transport=httpx.MockTransport(handle)))
        assert exc.value.code == code


def test_absolute_upstream_deadline(catalog, query_request, monkeypatch):
    monkeypatch.setattr(upstream, "UPSTREAM_TIMEOUT_SECONDS", 0.01)

    async def handle(_):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={})

    with pytest.raises(upstream.UpstreamFailure) as exc:
        asyncio.run(upstream.fetch_metadata(catalog, query_request, transport=httpx.MockTransport(handle)))
    assert exc.value.code == "upstream_timeout"

"""Fixed-origin, bounded upstream HTTP GET execution."""

from __future__ import annotations

import asyncio
import json

import httpx

from .catalog import Catalog


MAX_RESPONSE_BYTES = 24_000_000
UPSTREAM_TIMEOUT_SECONDS = 600


class UpstreamFailure(Exception):
    def __init__(self, code: str, status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(code)


async def fetch_metadata(
    catalog: Catalog,
    request: dict,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    url = catalog.origin + request["path"]
    try:
        async with asyncio.timeout(UPSTREAM_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(UPSTREAM_TIMEOUT_SECONDS),
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            ) as client:
                for attempt in range(2):
                    async with client.stream("GET", url, params=request["query_params"]) as response:
                        if response.is_redirect:
                            raise UpstreamFailure("upstream_redirect_rejected", response.status_code)
                        chunks = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise UpstreamFailure("upstream_response_too_large", response.status_code)
                            chunks.extend(chunk)
                        if (response.status_code == 429 or 500 <= response.status_code < 600) and attempt == 0:
                            await asyncio.sleep(1)
                            continue
                        try:
                            payload = json.loads(chunks)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            raise UpstreamFailure("upstream_invalid_json", response.status_code) from None
                        return {
                            "request": request,
                            "http_status": response.status_code,
                            "payload": payload,
                            "source": catalog.source,
                        }
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise UpstreamFailure("upstream_timeout") from exc
    except httpx.RequestError as exc:
        raise UpstreamFailure("upstream_transport_error") from exc
    raise UpstreamFailure("upstream_retry_exhausted")

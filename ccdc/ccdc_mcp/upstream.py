"""Bounded requests to the CCDC participating-resources REST API."""

from __future__ import annotations

import asyncio
import json

import httpx


API_ORIGIN = "https://datacatalog.ccdi.cancer.gov"
MAX_RESPONSE_BYTES = 24_000_000
UPSTREAM_DEADLINE_SECONDS = 45


class UpstreamFailure(Exception):
    def __init__(self, code: str, status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(code)


async def fetch_json(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[int, object]:
    """Fetch one fixed-origin JSON response; do not follow redirects or pages."""
    if method not in {"GET", "POST"} or not path.startswith("/service/dataresources/"):
        raise ValueError("Unsupported CCDC request")
    headers = {"Accept": "application/json"}
    kwargs: dict[str, object] = {"headers": headers}
    if method == "POST":
        headers["Content-Type"] = "application/json"
        kwargs["json"] = body
    try:
        async with asyncio.timeout(UPSTREAM_DEADLINE_SECONDS):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=5.0),
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            ) as client:
                for attempt in range(2):
                    async with client.stream(method, API_ORIGIN + path, **kwargs) as response:
                        if response.is_redirect:
                            raise UpstreamFailure("upstream_redirect_rejected", response.status_code)
                        chunks = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise UpstreamFailure("upstream_response_too_large", response.status_code)
                            chunks.extend(chunk)
                        if (response.status_code == 429 or response.status_code >= 500) and attempt == 0:
                            await asyncio.sleep(0.5)
                            continue
                        try:
                            payload = json.loads(chunks)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            raise UpstreamFailure("upstream_invalid_json", response.status_code) from None
                        return response.status_code, payload
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise UpstreamFailure("upstream_timeout") from exc
    except httpx.RequestError as exc:
        raise UpstreamFailure("upstream_transport_error") from exc
    raise UpstreamFailure("upstream_retry_exhausted")

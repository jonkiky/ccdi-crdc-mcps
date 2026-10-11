"""Streamable HTTP MCP access to CCDC participating-resource metadata."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from importlib.resources import files
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .upstream import API_ORIGIN, UpstreamFailure, fetch_json


mcp = MCPServer(
    "CCDC Participating Resources",
    description=(
        "Read-only access to the Childhood Cancer Data Catalog's participating "
        "resources, filter options, and linked dataset summaries."
    ),
    version=__version__,
)
upstream_slots = asyncio.Semaphore(16)


def _error(code: str, *, message: str | None = None, status: int | None = None) -> CallToolResult:
    content: dict[str, object] = {"error": code}
    if message is not None:
        content["message"] = message
    if status is not None:
        content["http_status"] = status
    return CallToolResult(content=[], structuredContent=content, isError=True)


def _source(path: str) -> dict[str, str]:
    return {
        "url": API_ORIGIN + path,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


def _valid_resource_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and value == value.strip()
        and value not in {".", ".."}
        and not any(ord(char) < 32 or char in "/\\" for char in value)
    )


def _valid_filter_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= 20
        and all(
            isinstance(item, str)
            and 0 < len(item) <= 100
            and item == item.strip()
            and not any(ord(char) < 32 for char in item)
            for item in value
        )
    )


def _valid_positive_int(value: object, maximum: int) -> bool:
    return type(value) is int and 1 <= value <= maximum


async def _read(
    method: str, path: str, body: dict | None = None, *, allow_missing_data: bool = False
) -> tuple[dict | None, CallToolResult | None]:
    try:
        async with upstream_slots:
            status, payload = await fetch_json(method, path, body=body)
    except UpstreamFailure as exc:
        return None, _error(exc.code, status=exc.status)
    if status != 200:
        return None, _error("upstream_http_error", status=status)
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "success"
        or ("data" not in payload and not allow_missing_data)
    ):
        return None, _error("upstream_unexpected_payload", status=status)
    return payload, None


@mcp.tool()
async def list_filter_options() -> CallToolResult:
    """List current Resource Type and Data Content Type values and counts.

    Use the exact names returned here when filtering search_resources. Counts
    are reported by the catalog and may overlap across content types.
    """
    path = "/service/dataresources/filters"
    payload, error = await _read("GET", path)
    if error:
        return error
    filters = payload["data"].get("searchFilters") if isinstance(payload["data"], dict) else None
    if not isinstance(filters, dict):
        return _error("upstream_unexpected_payload")
    return CallToolResult(
        content=[],
        structuredContent={"filters": filters, "source": _source(path)},
    )


@mcp.tool()
async def search_resources(
    resource_types: list[str] | None = None,
    data_content_types: list[str] | None = None,
    page: int = 1,
    page_size: int = 10,
) -> CallToolResult:
    """Search one page of participating resources by the catalog's two facets.

    Search is a read-only POST in the site's REST API. This tool requests one
    page only; use page_info.total and another page number to continue.

    Args:
        resource_types: Exact Resource Type values from list_filter_options.
        data_content_types: Exact Data Content Type values from list_filter_options.
        page: One-based page number, up to 10,000.
        page_size: Items per page, 1 through 50.
    """
    resource_types = [] if resource_types is None else resource_types
    data_content_types = [] if data_content_types is None else data_content_types
    if not _valid_filter_list(resource_types) or not _valid_filter_list(data_content_types):
        return _error("invalid_filters", message="Filters must be lists of up to 20 exact, nonempty names.")
    if not _valid_positive_int(page, 10_000) or not _valid_positive_int(page_size, 50):
        return _error("invalid_pagination", message="page must be 1-10000 and page_size must be 1-50.")
    path = "/service/dataresources/search"
    body = {
        "facet_filters": {
            "resource_type": resource_types,
            "data_content_type": data_content_types,
        },
        "pageInfo": {"page": page, "pageSize": page_size},
    }
    payload, error = await _read("POST", path, body)
    if error:
        return error
    data = payload["data"]
    if not isinstance(data, dict) or not isinstance(data.get("result"), list) or not isinstance(data.get("pageInfo"), dict):
        return _error("upstream_unexpected_payload")
    return CallToolResult(
        content=[],
        structuredContent={
            "resources": data["result"],
            "page_info": data["pageInfo"],
            "filters": body["facet_filters"],
            "source": _source(path),
        },
    )


@mcp.tool()
async def get_resource(resource_id: str) -> CallToolResult:
    """Get one participating-resource record by its exact catalog ID.

    IDs appear in search_resources results as data_resource_id. The response
    includes the catalog's description, content type, contact, and links.
    """
    if not _valid_resource_id(resource_id):
        return _error("invalid_resource_id", message="Use one exact resource ID without slashes or control characters.")
    path = "/service/dataresources/" + quote(resource_id, safe="")
    payload, error = await _read("GET", path, allow_missing_data=True)
    if error:
        return error
    if "data" not in payload:
        return _error("resource_not_found")
    if not isinstance(payload["data"], dict):
        return _error("upstream_unexpected_payload")
    return CallToolResult(
        content=[],
        structuredContent={"resource": payload["data"], "source": _source(path)},
    )


@mcp.tool()
async def list_resource_datasets(
    resource_id: str,
    offset: int = 0,
    limit: int = 50,
) -> CallToolResult:
    """List dataset summaries linked to one resource, with bounded output.

    The CCDC endpoint returns the full linked list; this tool slices its JSON
    response locally and exposes total and next_offset for navigation.
    """
    if not _valid_resource_id(resource_id):
        return _error("invalid_resource_id", message="Use one exact resource ID without slashes or control characters.")
    if type(offset) is not int or not 0 <= offset <= 1_000_000 or not _valid_positive_int(limit, 100):
        return _error("invalid_pagination", message="offset must be 0-1000000 and limit must be 1-100.")
    path = "/service/dataresources/" + quote(resource_id, safe="") + "/datasets"
    payload, error = await _read("GET", path)
    if error:
        return error
    if not isinstance(payload["data"], list):
        return _error("upstream_unexpected_payload")
    datasets = payload["data"]
    next_offset = offset + limit if offset + limit < len(datasets) else None
    return CallToolResult(
        content=[],
        structuredContent={
            "resource_id": resource_id,
            "datasets": datasets[offset : offset + limit],
            "total": len(datasets),
            "offset": offset,
            "next_offset": next_offset,
            "source": _source(path),
        },
    )


@mcp.resource("ccdc://reference/catalog", mime_type="text/markdown")
def catalog_reference() -> str:
    """Read bundled guidance on the catalog and participating resources."""
    return files("ccdc_mcp").joinpath("assets/catalog.md").read_text(encoding="utf-8")


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ready"})


def _csv_env(name: str, default: str = "") -> list[str]:
    return [part.strip() for part in os.environ.get(name, default).split(",") if part.strip()]


allowed_origins = _csv_env("CCDC_ALLOWED_ORIGINS")
allowed_hosts = _csv_env(
    "CCDC_ALLOWED_HOSTS", "localhost:8000,127.0.0.1:8000,localhost,127.0.0.1"
)
app = mcp.streamable_http_app(
    json_response=True,
    stateless_http=True,
    max_request_body_size=65_536,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    ),
)
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["POST", "GET", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type", "MCP-Protocol-Version", "Mcp-Method", "Mcp-Name",
            "Mcp-Session-Id", "Last-Event-ID",
        ],
        expose_headers=["Mcp-Session-Id"],
    )

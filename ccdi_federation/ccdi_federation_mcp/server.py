"""Remote Streamable HTTP MCP server for CCDI metadata."""

from __future__ import annotations

import asyncio
import json
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .catalog import Catalog
from .upstream import UpstreamFailure, fetch_metadata
from .validation import validate_query as validate_request


catalog = Catalog()  # Fail startup if bundled assets are missing or invalid.
mcp = MCPServer(
    "CCDI Federation API",
    description="Public, read-only CCDI Federation resource and bounded metadata GET requests.",
    version=__version__,
)
upstream_slots = asyncio.Semaphore(16)


def _csv_env(name: str, default: str = "") -> list[str]:
    return [part.strip() for part in os.environ.get(name, default).split(",") if part.strip()]


@mcp.tool(structured_output=True)
def list_operations(entity: str | None = None, kind: str | None = None) -> dict[str, object]:
    """Discover the GET operations available through this MCP server.

    Reads the bundled OpenAPI document; does not contact the CCDI API.

    Args:
        entity: Optional subject, sample, file, or other filter.
        kind: Optional collection, detail, count, summary, or metadata filter.

    Returns:
        Operation IDs, paths, short descriptions, pagination flags, total,
        and source provenance. Invalid filters return an explicit error.
    """
    return catalog.list_operations(entity, kind)


@mcp.tool(structured_output=True)
def describe_operation(operation_id: str) -> dict[str, object]:
    """Read the documented contract for one CCDI metadata GET operation.

    Use an operation ID from list_operations. This tool makes no network call.

    Args:
        operation_id: Exact operationId from the bundled OpenAPI document.

    Returns:
        Method, path, description, documented path and query parameters,
        response definitions, pagination policy, and source provenance.
        Unknown IDs return operation_not_found.
    """
    return catalog.describe_operation(operation_id)


@mcp.tool(structured_output=True)
def get_field_metadata(entity: str, field: str) -> dict[str, object]:
    """Read bundled metadata about one subject, sample, or file field.

    This is an offline reference lookup. It does not establish whether the
    field is a valid API query filter; describe_operation determines that.

    Args:
        entity: One of subject, sample, or file.
        field: Exact field name in that entity's bundled PV metadata.

    Returns:
        Formal name, description, caDSR link, controlled status, PV count,
        and source provenance. Unknown fields and fields without a bundled
        PV list are distinct states.
    """
    return catalog.get_field_metadata(entity, field)


@mcp.tool(structured_output=True)
def list_permissible_values(
    entity: str, field: str | None = None, offset: int = 0, limit: int = 100
) -> dict[str, object]:
    """List exact bundled permissible values (PVs) for one entity or field.

    This is an offline inventory, limited to subject, sample, and file.
    It does not search arbitrary terms, infer synonyms, or validate API
    filter values. With no field, values are ordered by field name in stable
    order; each entry includes its field and bundled PV details.

    Args:
        entity: Required subject, sample, or file entity.
        field: Optional exact field name; omit to traverse all controlled fields.
        offset: Zero-based position in the exact PV list; defaults to 0.
        limit: Maximum values in this page, from 1 through 200; defaults to 100.

    Returns:
        Exact values, total, next_offset for further pages, and provenance.
        Unsupported entities, unknown fields, and fields without PVs return
        distinct errors.
    """
    return catalog.list_permissible_values(entity, field, offset, limit)


@mcp.tool(structured_output=True)
def validate_query(
    operation_id: str,
    path_params: dict | None = None,
    query_params: dict | None = None,
) -> dict[str, object]:
    """Check a proposed metadata request against bundled OpenAPI rules.

    This tool does not contact the CCDI API. It checks the operation, named
    path and query parameters, types, safe path segments, and pagination.
    It leaves filter values unchanged and does not check them against PVs.

    Args:
        operation_id: Exact documented GET operation ID.
        path_params: Named values for path placeholders, when required.
        query_params: Named documented filters and pagination values.

    Returns:
        valid and executable flags, a normalized GET request when valid,
        specific validation errors, warnings, and source provenance.
        Paginated operations default to page 1 and per_page 20; per_page
        above 200 is rejected.
    """
    return validate_request(catalog, operation_id, path_params, query_params)


@mcp.tool()
async def query_metadata(
    operation_id: str,
    path_params: dict | None = None,
    query_params: dict | None = None,
) -> CallToolResult:
    """Run one documented CCDI metadata GET and return its complete JSON.

    Revalidates every call using the same rules as validate_query, then uses
    the fixed CCDI API origin. It fetches one response only, never follows
    pagination or file-download links, and preserves upstream node errors.
    Filter values are sent unchanged; bundled PVs are not a preflight check.

    Args:
        operation_id: Exact documented GET operation ID.
        path_params: Named values for required path placeholders, if any.
        query_params: Documented filters and pagination values, if any.

    Returns:
        Structured content containing the validated request, HTTP status,
        complete parsed JSON payload, and source provenance. Invalid input
        and upstream failures set the tool error flag. The live call has a
        10-minute deadline, a 24,000,000-byte decoded-response limit, and at most one
        retry for HTTP 429 or 5xx. No summaries or selected examples are added.
    """
    validation = validate_request(catalog, operation_id, path_params, query_params)
    if not validation["valid"]:
        return CallToolResult(content=[], structuredContent=validation, isError=True)
    try:
        async with upstream_slots:
            result = await fetch_metadata(catalog, validation["request"])
    except UpstreamFailure as exc:
        return CallToolResult(
            content=[],
            structuredContent={
                "error": exc.code,
                "http_status": exc.status,
                "request": validation["request"],
                "source": catalog.source,
            },
            isError=True,
        )
    return CallToolResult(
        content=[],
        structuredContent=result,
        isError=not 200 <= result["http_status"] < 300,
    )


@mcp.resource("ccdi://reference/operations", mime_type="application/json")
def operations_resource() -> str:
    """Read the immutable bundled operation index."""
    return json.dumps(catalog.list_operations(), separators=(",", ":"))


@mcp.resource("ccdi://reference/pv/{entity}/{field}", mime_type="application/json")
def pv_field_resource(entity: str, field: str) -> str:
    """Read exact bundled PVs for one controlled field."""
    return json.dumps(
        catalog.list_permissible_values(entity, field, offset=0, limit=200),
        separators=(",", ":"),
    )


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ready"})


allowed_origins = _csv_env("CCDI_ALLOWED_ORIGINS")
allowed_hosts = _csv_env(
    "CCDI_ALLOWED_HOSTS", "localhost:8000,127.0.0.1:8000,localhost,127.0.0.1"
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

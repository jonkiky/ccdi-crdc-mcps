"""Streamable HTTP MCP server for CCDI cBioPortal public REST reads."""

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
from .upstream import UpstreamFailure, fetch_api
from .validation import validate_request


catalog = Catalog()
mcp = MCPServer(
    "CCDI cBioPortal API",
    description="Read-only access to documented CCDI cBioPortal GET and POST /fetch operations. No domain permissible-value catalog is available.",
    version=__version__,
)
upstream_slots = asyncio.Semaphore(16)


def _csv_env(name: str, default: str = "") -> list[str]:
    return [part.strip() for part in os.environ.get(name, default).split(",") if part.strip()]


@mcp.tool(structured_output=True)
def list_operations(
    tag: str | None = None, method: str | None = None,
    offset: int = 0, limit: int = 100,
) -> dict[str, object]:
    """Discover documented cBioPortal reads by API tag or GET/POST method.

    POST operations are restricted to documented /fetch endpoints. The result
    includes pagination and operation IDs for describe_operation/query_api.
    """
    return catalog.list_operations(tag, method, offset, limit)


@mcp.tool(structured_output=True)
def describe_operation(operation_id: str) -> dict[str, object]:
    """Read the pinned OpenAPI contract for one operation, including parameters,
    request body, response schema, and the MCP pagination cap.
    """
    return catalog.describe_operation(operation_id)


@mcp.tool(structured_output=True)
def describe_schema(name: str) -> dict[str, object]:
    """Read one OpenAPI component schema by exact name. This describes fields
    and types; it does not provide domain permissible values.
    """
    return catalog.describe_schema(name)


@mcp.tool(structured_output=True)
def validate_query(
    operation_id: str, path_params: dict | None = None,
    query_params: dict | None = None, body: object = None,
) -> dict[str, object]:
    """Validate one proposed REST read locally, without calling cBioPortal.

    Checks the documented operation, parameter names and types, OpenAPI enum
    constraints, required JSON body, safe path segments, and page size. It does
    not validate free-text domain values against a PV catalog.
    """
    return validate_request(catalog, operation_id, path_params, query_params, body)


@mcp.tool()
async def query_api(
    operation_id: str, path_params: dict | None = None,
    query_params: dict | None = None, body: object = None,
) -> CallToolResult:
    """Run one documented cBioPortal GET or read-only POST /fetch request.

    Uses a fixed upstream origin and returns one complete JSON response. It
    never follows pagination or redirects. Values are sent unchanged after
    OpenAPI validation; no domain PV mapping is applied.
    """
    validation = validate_request(catalog, operation_id, path_params, query_params, body)
    if not validation["valid"]:
        return CallToolResult(content=[], structuredContent=validation, isError=True)
    try:
        async with upstream_slots:
            result = await fetch_api(catalog, validation["request"])
    except UpstreamFailure as exc:
        return CallToolResult(
            content=[],
            structuredContent={
                "error": exc.code, "http_status": exc.status,
                "request": validation["request"], "source": catalog.source,
            },
            isError=True,
        )
    return CallToolResult(
        content=[], structuredContent=result,
        isError=not 200 <= result["http_status"] < 300,
    )


@mcp.resource("cbioportal://reference/operations", mime_type="application/json")
def operations_resource() -> str:
    """Read the pinned operation index."""
    return json.dumps(catalog.list_operations(), separators=(",", ":"))


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ready"})


allowed_origins = _csv_env("CBIOPORTAL_ALLOWED_ORIGINS")
allowed_hosts = _csv_env(
    "CBIOPORTAL_ALLOWED_HOSTS", "localhost:8000,127.0.0.1:8000,localhost,127.0.0.1"
)
app = mcp.streamable_http_app(
    json_response=True, stateless_http=True, max_request_body_size=65_536,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts, allowed_origins=allowed_origins,
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

"""Standalone read-only CCDI DCC GraphQL MCP server over Streamable HTTP."""

from __future__ import annotations

import asyncio
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .catalog import Catalog
from .query import ALLOWED_ROOTS, ENTITY_ROOTS, UpstreamFailure, fetch_graphql, validate_graphql_query


catalog = Catalog()
mcp = MCPServer(
    "CCDI DCC GraphQL",
    description="Bounded read-only CCDI DCC GraphQL queries and exact CCDI model Enum values.",
    version=__version__,
)
upstream_slots = asyncio.Semaphore(8)


def _csv_env(name: str, default: str = "") -> list[str]:
    return [part.strip() for part in os.environ.get(name, default).split(",") if part.strip()]


def _tool_result(validation: dict, result: dict | None = None, error: UpstreamFailure | None = None) -> CallToolResult:
    if error:
        return CallToolResult(
            content=[], isError=True,
            structuredContent={
                "error": error.code, "http_status": error.status,
                "source": validation.get("source"),
            },
        )
    if result is None:
        return CallToolResult(content=[], isError=True, structuredContent=validation)
    payload = result["payload"]
    return CallToolResult(
        content=[],
        isError=not 200 <= result["http_status"] < 300 or bool(payload.get("errors")),
        structuredContent={**result, "pv_checks": validation["pv_checks"], "source": validation["source"]},
    )


async def _run(query: str, variables: dict | None = None) -> CallToolResult:
    validation = validate_graphql_query(catalog, query, variables)
    if not validation["valid"]:
        return _tool_result(validation)
    try:
        async with upstream_slots:
            result = await fetch_graphql(query, variables)
    except UpstreamFailure as exc:
        return _tool_result(validation, error=exc)
    return _tool_result(validation, result=result)


@mcp.tool(structured_output=True)
def list_query_fields(offset: int = 0, limit: int = 100) -> dict[str, object]:
    """List bundled GraphQL query fields; live availability is marked per field."""
    result = catalog.list_query_fields(offset, limit)
    for field in result.get("fields", []):
        field["enabled_for_live_mcp"] = field["name"] in ALLOWED_ROOTS
    return result


@mcp.tool(structured_output=True)
def describe_query_field(field: str) -> dict[str, object]:
    """Describe one exact GraphQL root field and its argument types offline."""
    result = catalog.describe_query_field(field)
    if "error" not in result:
        result["enabled_for_live_mcp"] = field in ALLOWED_ROOTS
    return result


@mcp.tool(structured_output=True)
def describe_type(type_name: str) -> dict[str, object]:
    """Describe fields of a GraphQL result or input type from the bundled schema."""
    return catalog.describe_type(type_name)


@mcp.tool(structured_output=True)
def list_properties(enum_only: bool = False, prefix: str | None = None,
                    offset: int = 0, limit: int = 100) -> dict[str, object]:
    """List CCDI model properties, with optional Enum-only or prefix filtering."""
    return catalog.list_properties(enum_only, prefix, offset, limit)


@mcp.tool(structured_output=True)
def describe_property(property_name: str) -> dict[str, object]:
    """Describe one exact property from the bundled CCDI model props YAML."""
    return catalog.describe_property(property_name)


@mcp.tool(structured_output=True)
def list_permissible_values(property_name: str, offset: int = 0,
                            limit: int = 100) -> dict[str, object]:
    """Page through exact CCDI model Enum values, preserving spelling and order."""
    return catalog.list_permissible_values(property_name, offset, limit)


@mcp.tool(structured_output=True)
def check_permissible_value(property_name: str, value: str) -> dict[str, object]:
    """Check exact CCDI model Enum membership; report YAML Strict separately."""
    return catalog.check_permissible_value(property_name, value)


@mcp.tool(structured_output=True)
def validate_graphql(query: str, variables: dict | None = None) -> dict[str, object]:
    """Validate one bounded, read-only query offline against the bundled schema."""
    return validate_graphql_query(catalog, query, variables)


@mcp.tool()
async def run_graphql_query(query: str, variables: dict | None = None) -> CallToolResult:
    """Execute one validated CCDI DCC query against the fixed endpoint.

    Entity list roots require explicit limit 1..100. Connection roots require
    explicit first 1..100. Relationship-edge roots and nested collections are
    disabled. Results preserve upstream data and errors.
    """
    return await _run(query, variables)


@mcp.tool()
async def list_studies(limit: int = 20, offset: int = 0) -> CallToolResult:
    """List one bounded page of DCC study identifiers and names."""
    query = """query Studies($limit: Int!, $offset: Int!) {
      studies(limit: $limit, offset: $offset) { guid study_id study_name }
    }"""
    return await _run(query, {"limit": limit, "offset": offset})


@mcp.tool()
async def search_participants(where: dict | None = None, first: int = 20,
                              after: str | None = None) -> CallToolResult:
    """Search a bounded participant connection using exact ParticipantWhere fields.

    Use describe_type('ParticipantWhere') for available filters. CCDI model
    Enum comparisons are advisory and do not rewrite filter values.
    """
    query = """query Participants($where: ParticipantWhere, $first: Int!, $after: String) {
      participantsConnection(where: $where, first: $first, after: $after) {
        totalCount pageInfo { hasNextPage endCursor }
        edges { node { guid participant_id sex_at_birth } }
      }
    }"""
    return await _run(query, {"where": where, "first": first, "after": after})


@mcp.tool()
async def count_entities(entity: str, where: dict | None = None) -> CallToolResult:
    """Count one DCC entity type, optionally filtered by its exact Where input."""
    if entity not in ENTITY_ROOTS:
        return _tool_result({"valid": False, "errors": ["unknown or disabled entity"]})
    root = f"{entity}Connection"
    where_type = catalog.schema.query_type.fields[root].args["where"].type
    query = f"query Count($where: {where_type}) {{ {root}(first: 1, where: $where) {{ totalCount }} }}"
    return await _run(query, {"where": where})


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ready", "schema_sha256": catalog.schema_sha256})


allowed_origins = _csv_env("CCDI_DCC_ALLOWED_ORIGINS")
allowed_hosts = _csv_env(
    "CCDI_DCC_ALLOWED_HOSTS", "localhost:8002,127.0.0.1:8002,localhost,127.0.0.1",
)
app = mcp.streamable_http_app(
    json_response=True,
    stateless_http=True,
    max_request_body_size=32_768,
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
        allow_headers=["Content-Type", "MCP-Protocol-Version", "Mcp-Method", "Mcp-Name",
                       "Mcp-Session-Id", "Last-Event-ID"],
        expose_headers=["Mcp-Session-Id"],
    )

"""Standalone C3DC GraphQL MCP server over Streamable HTTP."""

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
from .query import ALLOWED_ROOTS, UpstreamFailure, fetch_graphql, validate_graphql_query


catalog = Catalog()
mcp = MCPServer(
    "C3DC GraphQL",
    description="Read-only Clinical Commons GraphQL queries and exact CCDI model Enum values.",
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
    """List public GraphQL query-root fields from the bundled C3DC schema.

    This offline reference tool includes fields that are not enabled for live
    MCP execution. Use describe_query_field for exact arguments and types.
    """
    result = catalog.list_query_fields(offset, limit)
    if "fields" in result:
        for field in result["fields"]:
            field["enabled_for_live_mcp"] = field["name"] in ALLOWED_ROOTS
    return result


@mcp.tool(structured_output=True)
def describe_query_field(field: str) -> dict[str, object]:
    """Describe one exact GraphQL root field and its typed arguments offline."""
    result = catalog.describe_query_field(field)
    if "error" not in result:
        result["enabled_for_live_mcp"] = field in ALLOWED_ROOTS
    return result


@mcp.tool(structured_output=True)
def describe_type(type_name: str) -> dict[str, object]:
    """Describe fields of a named GraphQL result type from the bundled schema."""
    return catalog.describe_type(type_name)


@mcp.tool(structured_output=True)
def list_properties(
    enum_only: bool = False, prefix: str | None = None,
    offset: int = 0, limit: int = 100,
) -> dict[str, object]:
    """List properties from the bundled CCDI model props YAML."""
    return catalog.list_properties(enum_only, prefix, offset, limit)


@mcp.tool(structured_output=True)
def describe_property(property_name: str) -> dict[str, object]:
    """Describe an exact property from the bundled CCDI model props YAML."""
    return catalog.describe_property(property_name)


@mcp.tool(structured_output=True)
def list_permissible_values(
    property_name: str, offset: int = 0, limit: int = 100,
) -> dict[str, object]:
    """Page through exact CCDI model Enum values, preserving order and spelling."""
    return catalog.list_permissible_values(property_name, offset, limit)


@mcp.tool(structured_output=True)
def check_permissible_value(property_name: str, value: str) -> dict[str, object]:
    """Check exact CCDI model Enum membership; report the YAML Strict flag separately."""
    return catalog.check_permissible_value(property_name, value)


@mcp.tool(structured_output=True)
def validate_graphql(query: str, variables: dict | None = None) -> dict[str, object]:
    """Validate one read-only GraphQL query against the bundled schema and execution bounds.

    Exact-name Enum checks are advisory because a model property does not by
    itself establish the GraphQL API's filter semantics. No network call.
    """
    return validate_graphql_query(catalog, query, variables)


@mcp.tool()
async def run_graphql_query(
    query: str, variables: dict | None = None,
) -> CallToolResult:
    """Execute one validated, bounded, read-only GraphQL query against C3DC.

    Use list_query_fields and describe_query_field to discover the contract.
    Paginated roots require first 1..100 and offset 0..10000. The server
    rejects mutations, arbitrary introspection, bulk manifest and ID roots,
    deep queries, redirects, and responses above 2 MB. It preserves upstream
    data and GraphQL errors without rewriting PV values.
    """
    return await _run(query, variables)


@mcp.tool()
async def get_overview_counts() -> CallToolResult:
    """Get current C3DC participant, disease, and study counts in one query."""
    return await _run("{ numberOfParticipants numberOfDiseases numberOfStudies }")


@mcp.tool()
async def list_studies(first: int = 20, offset: int = 0) -> CallToolResult:
    """List a bounded page of C3DC studies and their public record counts."""
    query = """query Studies($first: Int!, $offset: Int!) {
      studiesListing(first: $first, offset: $offset) {
        study_id study_name num_of_participants num_of_diagnoses num_of_samples num_of_files
      }
    }"""
    return await _run(query, {"first": first, "offset": offset})


@mcp.tool()
async def search_participants(filters: dict | None = None) -> CallToolResult:
    """Count C3DC participants, studies, samples, files, and diagnoses for exact GraphQL filters.

    `filters` maps GraphQL argument names to bounded lists of exact values.
    Use describe_query_field('searchParticipants') to discover arguments and
    list_permissible_values to inspect model Enum values. Unknown filter names
    and wrong types are rejected; Enum comparisons are advisory only.
    """
    if filters is None:
        filters = {}
    if not isinstance(filters, dict) or len(filters) > 10:
        return _tool_result({"valid": False, "errors": ["filters must be an object with at most 10 fields"]})
    available = catalog.schema.query_type.fields["searchParticipants"].args
    if any(name not in available or not str(available[name].type).startswith("[") for name in filters):
        return _tool_result({"valid": False, "errors": ["unknown or unsupported searchParticipants filter"]})
    declarations = ", ".join(f"${name}: {available[name].type}" for name in filters)
    arguments = ", ".join(f"{name}: ${name}" for name in filters)
    header = f"query Search({declarations})" if filters else "query Search"
    query = f"""{header} {{
      searchParticipants({arguments}) {{
        numberOfParticipants numberOfStudies numberOfSamples numberOfFiles numberOfDiagnosis
      }}
    }}""" if filters else """query Search {
      searchParticipants {
        numberOfParticipants numberOfStudies numberOfSamples numberOfFiles numberOfDiagnosis
      }
    }"""
    return await _run(query, filters)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ready", "schema_sha256": catalog.schema_sha256})


allowed_origins = _csv_env("C3DC_ALLOWED_ORIGINS")
allowed_hosts = _csv_env(
    "C3DC_ALLOWED_HOSTS", "localhost:8001,127.0.0.1:8001,localhost,127.0.0.1",
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
        allow_headers=[
            "Content-Type", "MCP-Protocol-Version", "Mcp-Method", "Mcp-Name",
            "Mcp-Session-Id", "Last-Event-ID",
        ],
        expose_headers=["Mcp-Session-Id"],
    )

"""Standalone read-only Population Science GraphQL MCP over Streamable HTTP."""

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
    "Population Science GraphQL",
    description="Bounded study-level GraphQL metadata and exact PopSci model Enum values.",
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
    """List bundled GraphQL query fields and their live MCP availability."""
    result = catalog.list_query_fields(offset, limit)
    for field in result.get("fields", []):
        field["enabled_for_live_mcp"] = field["name"] in ALLOWED_ROOTS
    return result


@mcp.tool(structured_output=True)
def describe_query_field(field: str) -> dict[str, object]:
    """Describe exact arguments and type of one GraphQL root field offline."""
    result = catalog.describe_query_field(field)
    if "error" not in result:
        result["enabled_for_live_mcp"] = field in ALLOWED_ROOTS
    return result


@mcp.tool(structured_output=True)
def describe_type(type_name: str) -> dict[str, object]:
    """Describe fields of a named GraphQL result or input type offline."""
    return catalog.describe_type(type_name)


@mcp.tool(structured_output=True)
def list_properties(enum_only: bool = False, prefix: str | None = None,
                    offset: int = 0, limit: int = 100) -> dict[str, object]:
    """List exact properties from the bundled PopSci model YAML."""
    return catalog.list_properties(enum_only, prefix, offset, limit)


@mcp.tool(structured_output=True)
def describe_property(property_name: str) -> dict[str, object]:
    """Describe one exact PopSci model property offline."""
    return catalog.describe_property(property_name)


@mcp.tool(structured_output=True)
def list_permissible_values(property_name: str, offset: int = 0,
                            limit: int = 100) -> dict[str, object]:
    """Page through exact PopSci model Enum values, preserving spelling and order."""
    return catalog.list_permissible_values(property_name, offset, limit)


@mcp.tool(structured_output=True)
def check_permissible_value(property_name: str, value: str) -> dict[str, object]:
    """Check exact PopSci model Enum membership and report YAML Strict separately."""
    return catalog.check_permissible_value(property_name, value)


@mcp.tool(structured_output=True)
def validate_graphql(query: str, variables: dict | None = None) -> dict[str, object]:
    """Validate one bounded study metadata query offline against the bundled schema."""
    return validate_graphql_query(catalog, query, variables)


@mcp.tool()
async def run_graphql_query(query: str, variables: dict | None = None) -> CallToolResult:
    """Execute one validated read-only Population Science study metadata query.

    Use schema discovery to find fields. Paginated roots require first 1..100.
    Subject-level roots, bulk ID roots, file URLs, and nested record lists are
    disabled. Results preserve upstream data and GraphQL errors.
    """
    return await _run(query, variables)


@mcp.tool()
async def get_study_overview() -> CallToolResult:
    """Get current portal study, file, diagnosis, and data-volume summary."""
    return await _run("{ searchStudies { numberOfStudies numberOfDataFiles numberOfDiagnosis dataVolume } }")


@mcp.tool()
async def list_studies(first: int = 20, offset: int = 0,
                       study_design: list[str] | None = None,
                       study_status: list[str] | None = None) -> CallToolResult:
    """List one page of study summaries with optional exact model PV filters."""
    declarations = ["$first: Int!", "$offset: Int!"]
    arguments = ["first: $first", "offset: $offset"]
    variables = {"first": first, "offset": offset}
    for name, value in (("study_design", study_design), ("study_status", study_status)):
        if value is not None:
            declarations.append(f"${name}: [String]")
            arguments.append(f"{name}: ${name}")
            variables[name] = value
    query = f"""query Studies({', '.join(declarations)}) {{
      tabStudy({', '.join(arguments)}) {{
        study_name study_short_name study_design study_status number_of_participants
      }}
    }}"""
    return await _run(query, variables)


@mcp.tool()
async def list_study_files(first: int = 20, offset: int = 0,
                           study_short_name: list[str] | None = None) -> CallToolResult:
    """List bounded study file metadata without download or storage URLs."""
    declarations = ["$first: Int!", "$offset: Int!"]
    arguments = ["first: $first", "offset: $offset"]
    variables = {"first": first, "offset": offset}
    if study_short_name is not None:
        declarations.append("$study_short_name: [String]")
        arguments.append("study_short_name: $study_short_name")
        variables["study_short_name"] = study_short_name
    query = f"""query Files({', '.join(declarations)}) {{
      studyFiles({', '.join(arguments)}) {{
        study_short_name data_file_name data_file_type data_file_format
        data_file_access_control data_file_size data_file_uuid
      }}
    }}"""
    return await _run(query, variables)


@mcp.tool()
async def get_study_demographics(study_short_name: list[str], first: int = 20,
                                 offset: int = 0) -> CallToolResult:
    """Get bounded, grouped demographic summaries for named studies."""
    query = """query Demographics($study_short_name: [String], $first: Int!, $offset: Int!) {
      studyDemographics(study_short_name: $study_short_name, first: $first, offset: $offset) {
        study_name study_short_name number_of_participants
        participant_races { group subjects }
        participant_ethnicities { group subjects }
        participant_sexes { group subjects }
      }
    }"""
    return await _run(query, {"study_short_name": study_short_name, "first": first, "offset": offset})


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ready", "schema_sha256": catalog.schema_sha256})


allowed_origins = _csv_env("POPSCI_ALLOWED_ORIGINS")
allowed_hosts = _csv_env(
    "POPSCI_ALLOWED_HOSTS", "localhost:8003,127.0.0.1:8003,localhost,127.0.0.1",
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

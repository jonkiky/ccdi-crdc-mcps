"""Standalone Integrated Canine Data Commons MCP server over Streamable HTTP."""

from __future__ import annotations

import asyncio
import json
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .catalog import Catalog
from .query import ALLOWED_ROOTS, UpstreamFailure, fetch_graphql, validate_graphql_query


catalog = Catalog()
mcp = MCPServer(
    "Integrated Canine Data Commons GraphQL",
    description="Read-only, bounded public ICDC metadata queries. No permissible-value catalog is used.",
    version=__version__,
)
upstream_slots = asyncio.Semaphore(8)


def _csv_env(name: str, default: str = "") -> list[str]:
    return [part.strip() for part in os.environ.get(name, default).split(",") if part.strip()]


def _tool_result(
    validation: dict, result: dict | None = None, error: UpstreamFailure | None = None,
) -> CallToolResult:
    if error:
        body = {
            "error": error.code, "http_status": error.status,
            "source": validation.get("source"),
        }
        is_error = True
    elif result is None:
        body = validation
        is_error = True
    else:
        payload = result["payload"]
        body = {**result, "source": validation["source"]}
        is_error = not 200 <= result["http_status"] < 300 or bool(payload.get("errors"))
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(body, ensure_ascii=False, separators=(",", ":")))],
        isError=is_error,
        structuredContent=body,
    )


async def _run(query: str, variables: dict | None = None) -> CallToolResult:
    validation = validate_graphql_query(catalog, query, variables)
    if not validation["valid"]:
        return _tool_result(validation)
    try:
        async with upstream_slots:
            result = await fetch_graphql(query, variables, validation["operation_name"])
    except UpstreamFailure as exc:
        return _tool_result(validation, error=exc)
    return _tool_result(validation, result=result)


@mcp.tool(structured_output=True)
def list_query_fields(offset: int = 0, limit: int = 100) -> dict[str, object]:
    """List root fields from the bundled ICDC schema, including disabled roots."""
    result = catalog.list_query_fields(offset, limit)
    if "fields" in result:
        for field in result["fields"]:
            field["enabled_for_live_mcp"] = field["name"] in ALLOWED_ROOTS
    return result


@mcp.tool(structured_output=True)
def describe_query_field(field: str) -> dict[str, object]:
    """Describe a root field's return type and arguments from the bundled schema."""
    result = catalog.describe_query_field(field)
    if "error" not in result:
        result["enabled_for_live_mcp"] = field in ALLOWED_ROOTS
    return result


@mcp.tool(structured_output=True)
def describe_type(type_name: str, offset: int = 0, limit: int = 100) -> dict[str, object]:
    """Page through fields of a named GraphQL type from the bundled schema."""
    return catalog.describe_type(type_name, offset, limit)


@mcp.tool(structured_output=True)
def validate_graphql(query: str, variables: dict | None = None) -> dict[str, object]:
    """Validate one bounded read-only query offline, without contacting ICDC."""
    return validate_graphql_query(catalog, query, variables)


@mcp.tool()
async def run_graphql_query(query: str, variables: dict | None = None) -> CallToolResult:
    """Run one validated public metadata query against the fixed ICDC endpoint.

    Enabled roots are shown by list_query_fields. Paginated roots require an
    explicit first of 1..100; offset may be 0..10000. Mutations, introspection,
    manifests, downloads, and caller-selected URLs are not available.
    """
    return await _run(query, variables)


@mcp.tool()
async def get_overview_counts() -> CallToolResult:
    """Get ICDC schema version and public program, study, case, sample, and file counts."""
    return await _run("""query OverviewCounts {
      schemaVersion numberOfPrograms numberOfStudies numberOfCases
      numberOfSamples numberOfFiles numberOfStudyFiles numberOfAliquots
      volumeOfData
    }""")


@mcp.tool()
async def list_studies(first: int = 20, offset: int = 0) -> CallToolResult:
    """List one page of public ICDC studies with IDs and record counts."""
    query = """query Studies($first: Int, $offset: Int) {
      studiesByProgram(first: $first, offset: $offset) {
        program_id clinical_study_designation clinical_study_name
        clinical_study_type accession_id numberOfCases
        numberOfStudyFiles numberOfCaseFiles
      }
    }"""
    return await _run(query, {"first": first, "offset": offset})


@mcp.tool()
async def list_cases(first: int = 20, offset: int = 0) -> CallToolResult:
    """List one page of public ICDC case metadata."""
    query = """query Cases($first: Int, $offset: Int) {
      caseOverview(first: $first, offset: $offset) {
        case_id study_code breed diagnosis stage_of_disease sex
      }
    }"""
    return await _run(query, {"first": first, "offset": offset})


@mcp.tool()
async def search_icdc(term: str, first: int = 10, offset: int = 0) -> CallToolResult:
    """Search public ICDC programs, studies, cases, samples, and files."""
    query = """query SearchICDC($term: String, $first: Int, $offset: Int) {
      globalSearch(input: $term, first: $first, offset: $offset) {
        program_count study_count case_count sample_count file_count
        programs { program_id program_name }
        studies { clinical_study_designation clinical_study_name accession_id }
        cases { case_id clinical_study_designation disease_term breed }
        samples { sample_id case_id sample_site }
        files { file_name file_type case_id sample_id }
      }
    }"""
    return await _run(query, {"term": term, "first": first, "offset": offset})


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ready", "schema_sha256": catalog.schema_sha256})


allowed_origins = _csv_env("ICDC_ALLOWED_ORIGINS")
allowed_hosts = _csv_env(
    "ICDC_ALLOWED_HOSTS", "localhost:8004,127.0.0.1:8004,localhost,127.0.0.1",
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

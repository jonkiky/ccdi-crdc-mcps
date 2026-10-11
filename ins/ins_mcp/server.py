"""Standalone Index of NCI Studies GraphQL MCP server over Streamable HTTP."""

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
    "Index of NCI Studies GraphQL",
    description="Read-only, bounded GraphQL access to the public Index of NCI Studies.",
    version=__version__,
)
upstream_slots = asyncio.Semaphore(8)


def _csv_env(name: str, default: str = "") -> list[str]:
    return [part.strip() for part in os.environ.get(name, default).split(",") if part.strip()]


def _tool_result(validation: dict, result: dict | None = None, error: UpstreamFailure | None = None) -> CallToolResult:
    if error:
        return CallToolResult(
            content=[], isError=True,
            structuredContent={"error": error.code, "http_status": error.status, "source": validation.get("source")},
        )
    if result is None:
        return CallToolResult(content=[], isError=True, structuredContent=validation)
    payload = result["payload"]
    return CallToolResult(
        content=[],
        isError=not 200 <= result["http_status"] < 300 or bool(payload.get("errors")),
        structuredContent={**result, "source": validation["source"]},
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
    """List public GraphQL query-root fields from the bundled Study Catalog schema.

    This offline reference includes roots that are not enabled for live MCP
    execution. Use describe_query_field for exact arguments and types.
    """
    result = catalog.list_query_fields(offset, limit)
    if "fields" in result:
        for field in result["fields"]:
            field["enabled_for_live_mcp"] = field["name"] in ALLOWED_ROOTS
    return result


@mcp.tool(structured_output=True)
def describe_query_field(field: str) -> dict[str, object]:
    """Describe a GraphQL root field and its typed arguments offline."""
    result = catalog.describe_query_field(field)
    if "error" not in result:
        result["enabled_for_live_mcp"] = field in ALLOWED_ROOTS
    return result


@mcp.tool(structured_output=True)
def describe_type(type_name: str, offset: int = 0, limit: int = 100) -> dict[str, object]:
    """Page through fields of a GraphQL result type from the bundled schema."""
    return catalog.describe_type(type_name, offset, limit)


@mcp.tool(structured_output=True)
def validate_graphql(query: str, variables: dict | None = None) -> dict[str, object]:
    """Validate one named, read-only GraphQL query offline against the schema and MCP limits."""
    return validate_graphql_query(catalog, query, variables)


@mcp.tool()
async def run_graphql_query(query: str, variables: dict | None = None) -> CallToolResult:
    """Execute one validated, bounded, named GraphQL query against Study Catalog.

    Paginated roots require first 1..100 and offset 0..10000. The server
    rejects mutations, arbitrary introspection, bulk roots, deep queries,
    redirects, and responses above 2 MB. The upstream URL is fixed.
    """
    return await _run(query, variables)


@mcp.tool()
async def get_catalog_counts() -> CallToolResult:
    """Get current counts for INS programs, projects, grants, publications, and datasets."""
    return await _run("""query CatalogCounts {
      stats { numberOfPrograms numberOfProjects numberOfGrants numberOfPublications }
      numberOfDatasets
    }""")


@mcp.tool()
async def list_programs(first: int = 20, offset: int = 0) -> CallToolResult:
    """List a bounded page of NCI programs with names, acronyms, and links."""
    query = """query Programs($first: Int, $offset: Int) {
      programsOverview(first: $first, offset: $offset) {
        program_id program_name program_acronym program_link
      }
    }"""
    return await _run(query, {"first": first, "offset": offset})


@mcp.tool()
async def list_projects(
    first: int = 20, offset: int = 0, program_ids: list[str] | None = None,
) -> CallToolResult:
    """List a bounded page of NCI projects, optionally filtered by program IDs."""
    query = """query Projects($first: Int, $offset: Int, $program_ids: [String]) {
      projectsOverview(first: $first, offset: $offset, program_ids: $program_ids) {
        project_id project_title project_org_name program_names program_ids
      }
    }"""
    return await _run(query, {
        "first": first, "offset": offset,
        "program_ids": [] if program_ids is None else program_ids,
    })


@mcp.tool()
async def search_projects(
    program_ids: list[str] | None = None,
    program_names: list[str] | None = None,
    focus_area: list[str] | None = None,
    cancer_type: list[str] | None = None,
) -> CallToolResult:
    """Count projects and related outputs using exact GraphQL search filters.

    These are API filter strings; the INS GraphQL API does not publish a model
    permissible-value catalog through this server.
    """
    query = """query SearchProjects(
      $program_ids: [String], $program_names: [String],
      $focus_area: [String], $cancer_type: [String]
    ) {
      searchProjects(
        program_ids: $program_ids, program_names: $program_names,
        focus_area: $focus_area, cancer_type: $cancer_type
      ) {
        numberOfPrograms numberOfProjects numberOfGrants numberOfPublications
      }
    }"""
    return await _run(query, {
        "program_ids": [] if program_ids is None else program_ids,
        "program_names": [] if program_names is None else program_names,
        "focus_area": [] if focus_area is None else focus_area,
        "cancer_type": [] if cancer_type is None else cancer_type,
    })


@mcp.tool()
async def get_project_details(project_id: str) -> CallToolResult:
    """Get public details for one exact NCI project ID."""
    query = """query ProjectDetails($project_id: String) {
      projectDetails(project_id: $project_id) {
        project_id project_title project_abstract_text project_org_name
        project_start_date project_end_date program_ids program_acronyms
      }
    }"""
    return await _run(query, {"project_id": project_id})


@mcp.tool()
async def get_dataset_details(dataset_uuid: str) -> CallToolResult:
    """Get public metadata for one exact INS dataset UUID."""
    query = """query DatasetDetails($dataset_uuid: String!) {
      datasetDetails(dataset_uuid: $dataset_uuid) {
        dataset_uuid dataset_title description participant_count sample_count
        primary_disease dataset_source_repo dataset_source_url release_date
      }
    }"""
    return await _run(query, {"dataset_uuid": dataset_uuid})


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ready", "schema_sha256": catalog.schema_sha256})


allowed_origins = _csv_env("INS_ALLOWED_ORIGINS")
allowed_hosts = _csv_env(
    "INS_ALLOWED_HOSTS", "localhost:8002,127.0.0.1:8002,localhost,127.0.0.1",
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

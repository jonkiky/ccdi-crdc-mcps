"""Standalone Clinical Data Commons GraphQL MCP server over Streamable HTTP."""

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
    "Clinical Data Commons GraphQL",
    description="Read-only, bounded public metadata queries for Clinical Data Commons.",
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
    """List public GraphQL root fields from the bundled CTDC schema.

    Discovery includes roots not enabled for live MCP execution.
    """
    result = catalog.list_query_fields(offset, limit)
    if "fields" in result:
        for field in result["fields"]:
            field["enabled_for_live_mcp"] = field["name"] in ALLOWED_ROOTS
    return result


@mcp.tool(structured_output=True)
def describe_query_field(field: str) -> dict[str, object]:
    """Describe one GraphQL root field and its typed arguments offline."""
    result = catalog.describe_query_field(field)
    if "error" not in result:
        result["enabled_for_live_mcp"] = field in ALLOWED_ROOTS
    return result


@mcp.tool(structured_output=True)
def describe_type(type_name: str, offset: int = 0, limit: int = 100) -> dict[str, object]:
    """Page through fields of a named GraphQL result type offline."""
    return catalog.describe_type(type_name, offset, limit)


@mcp.tool(structured_output=True)
def validate_graphql(query: str, variables: dict | None = None) -> dict[str, object]:
    """Validate one bounded, read-only GraphQL query without a network call."""
    return validate_graphql_query(catalog, query, variables)


@mcp.tool()
async def run_graphql_query(query: str, variables: dict | None = None) -> CallToolResult:
    """Execute one validated metadata query against the fixed CTDC GraphQL endpoint.

    Paginated roots require first 1..100 and offset 0..10000. Mutations,
    arbitrary introspection, bulk file and download roots, deep queries,
    redirects, and responses above 2 MB are rejected.
    """
    return await _run(query, variables)


@mcp.tool()
async def get_home_counts() -> CallToolResult:
    """Get public participant, diagnosis, therapy, and data file counts."""
    return await _run("""query HomeCounts {
      getHomePage {
        numberOfParticipants numberOfDiagnoses numberOfTherapies
        numberOfTargeted numberOfNonTargeted dataFileCount
      }
    }""")


@mcp.tool()
async def list_studies() -> CallToolResult:
    """List public CTDC studies with identifiers and record counts."""
    return await _run("""query Studies {
      getAllStudies {
        study_short_name study_name study_id study_accession
        participant_count study_file_count
      }
    }""")


@mcp.tool()
async def get_study_details(study_short_name: str) -> CallToolResult:
    """Get public metadata for one exact CTDC study short name."""
    query = """query StudyDetails($study_short_name: [String]) {
      studyByStudyShortName(study_short_name: $study_short_name) {
        study_short_name study_name study_id study_accession
        study_description study_type dates_of_conduct
        participant_count study_file_count participant_file_count
      }
    }"""
    return await _run(query, {"study_short_name": [study_short_name]})


@mcp.tool()
async def get_study_diagnoses(study_short_name: str) -> CallToolResult:
    """List public diagnosis terms reported for one study."""
    query = """query StudyDiagnoses($study_short_name: [String]) {
      studyDiagnosisByStudyShortName(study_short_name: $study_short_name) {
        study_short_name ctep_disease_terms
      }
    }"""
    return await _run(query, {"study_short_name": [study_short_name]})


@mcp.tool()
async def get_study_specimen_summary(study_short_name: str) -> CallToolResult:
    """Get specimen counts by type and timepoint for one study."""
    query = """query StudySpecimens($study_short_name: [String]) {
      StudySpecimenByStudyShortName(study_short_name: $study_short_name) {
        study_short_name specimen_count
        specimen_types { group count }
        specimen_timepoints { group count }
      }
    }"""
    return await _run(query, {"study_short_name": [study_short_name]})


@mcp.tool()
async def get_study_clinical_counts(study_short_name: str) -> CallToolResult:
    """Get public clinical record and participant counts for one study."""
    query = """query StudyClinicalCounts($study_short_name: [String]) {
      clinicalData(study_short_name: $study_short_name) {
        study_short_name diagnosisNodeCount diagnosisParticipantCount
        demographicNodeCount demographicParticipantCount
        exposureNodeCount exposureParticipantCount
        specimenNodeCount specimenParticipantCount
        participantStatusNodeCount participantStatusParticipantCount
      }
    }"""
    return await _run(query, {"study_short_name": [study_short_name]})


@mcp.tool()
async def get_study_trial_counts(study_short_name: str) -> CallToolResult:
    """Get public therapy, radiotherapy, and surgery counts for one study."""
    query = """query StudyTrialCounts($study_short_name: [String]) {
      clinicalTrialData(study_short_name: $study_short_name) {
        study_short_name targetedTherapyNodeCount targetedTherapyParticipantCount
        nonTargetedTherapyNodeCount nonTargetedTherapyParticipantCount
        radiotherapyNodeCount radiotherapyParticipantCount
        surgeryNodeCount surgeryParticipantCount
      }
    }"""
    return await _run(query, {"study_short_name": [study_short_name]})


@mcp.tool()
async def search_participants(filters: dict | None = None) -> CallToolResult:
    """Count participants and related records using exact GraphQL filter names.

    Filters map API argument names to lists of strings. No model PV catalog is
    available; use describe_query_field('searchParticipants') for names and types.
    """
    if filters is None:
        filters = {}
    if not isinstance(filters, dict) or len(filters) > 10:
        return _tool_result({"valid": False, "errors": ["filters must be an object with at most 10 fields"]})
    available = catalog.schema.query_type.fields["searchParticipants"].args
    if any(name not in available or str(available[name].type) != "[String]" for name in filters):
        return _tool_result({"valid": False, "errors": ["unknown or unsupported searchParticipants filter"]})
    declarations = ", ".join(f"${name}: [String]" for name in filters)
    arguments = ", ".join(f"{name}: ${name}" for name in filters)
    header = f"query SearchParticipants({declarations})" if filters else "query SearchParticipants"
    field = f"searchParticipants({arguments})" if filters else "searchParticipants"
    query = f"""{header} {{
      {field} {{
        numberOfStudies numberOfParticipants numberOfDiagnoses
        numberOfTargetedTherapies numberOfSpecimens numberOfFiles numberOfStudyFiles
      }}
    }}"""
    return await _run(query, filters)


@mcp.tool()
async def list_participants(
    first: int = 20, offset: int = 0, study_short_name: list[str] | None = None,
) -> CallToolResult:
    """List one bounded page of public participant metadata."""
    query = """query Participants($first: Int, $offset: Int, $study_short_name: [String]) {
      participantOverview(first: $first, offset: $offset, study_short_name: $study_short_name) {
        participant_id study_short_name ctep_disease_term
        sex race age_at_enrollment
      }
    }"""
    return await _run(query, {
        "first": first, "offset": offset,
        "study_short_name": [] if study_short_name is None else study_short_name,
    })


@mcp.tool()
async def list_files(
    first: int = 20, offset: int = 0, study_short_name: list[str] | None = None,
) -> CallToolResult:
    """List one bounded page of public file metadata without downloading files."""
    query = """query Files($first: Int, $offset: Int, $study_short_name: [String]) {
      fileOverview(first: $first, offset: $offset, study_short_name: $study_short_name) {
        data_file_uuid data_file_name data_file_type data_file_format
        association study_short_name
      }
    }"""
    return await _run(query, {
        "first": first, "offset": offset,
        "study_short_name": [] if study_short_name is None else study_short_name,
    })


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ready", "schema_sha256": catalog.schema_sha256})


allowed_origins = _csv_env("CTDC_ALLOWED_ORIGINS")
allowed_hosts = _csv_env(
    "CTDC_ALLOWED_HOSTS", "localhost:8003,127.0.0.1:8003,localhost,127.0.0.1",
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

"""ICDC GraphQL safety checks using the pinned schema and mocked transport."""

import json

import httpx
import pytest

from icdc_mcp.catalog import Catalog, GRAPHQL_URL
from icdc_mcp.query import (
    ALLOWED_ROOTS, MAX_RESPONSE_BYTES, UpstreamFailure,
    fetch_graphql, validate_graphql_query,
)


@pytest.fixture(scope="module")
def catalog():
    return Catalog()


def test_allowed_roots_are_present_and_source_is_icdc(catalog):
    assert ALLOWED_ROOTS <= set(catalog.schema.query_type.fields)
    assert catalog.source()["graphql_endpoint"] == GRAPHQL_URL
    assert GRAPHQL_URL == "https://caninecommons.cancer.gov/v1/graphql/"


def test_read_only_counts_and_bounded_collection_queries(catalog):
    counts = validate_graphql_query(catalog, "query Counts { schemaVersion numberOfStudies numberOfCases }")
    assert counts["valid"]
    assert counts["operation_name"] == "Counts"

    studies = """query Studies($first:Int!,$offset:Int!){
      studiesByProgram(first:$first,offset:$offset){program_id clinical_study_name}
    }"""
    assert validate_graphql_query(catalog, studies, {"first": 2, "offset": 0})["valid"]
    assert not validate_graphql_query(catalog, studies, {"first": 0, "offset": 0})["valid"]
    assert not validate_graphql_query(catalog, studies, {"first": 101, "offset": 0})["valid"]
    assert not validate_graphql_query(catalog, studies, {"first": True, "offset": 0})["valid"]
    assert not validate_graphql_query(catalog, studies, {"first": 2, "offset": -1})["valid"]
    assert not validate_graphql_query(catalog, studies, {"first": 2, "offset": 10_001})["valid"]
    assert not validate_graphql_query(catalog, "{studiesByProgram{program_id}}") ["valid"]
    assert validate_graphql_query(
        catalog, "query Cases($first:Int!){caseOverview(first:$first){case_id}}", {"first": 1},
    )["valid"]


def test_all_overview_scalars_fit_one_query_but_data_roots_stay_capped(catalog):
    overview = """query Overview {
      schemaVersion numberOfStudies numberOfCases numberOfSamples
      numberOfFiles numberOfStudyFiles numberOfPrograms
      numberOfAliquots volumeOfData
    }"""
    assert validate_graphql_query(catalog, overview)["valid"]
    too_many_data_roots = """query Expanded {
      studiesByProgram(first:1){program_id}
      caseOverview(first:1){case_id}
      studyDetail(first:1,study_code:"ABC"){study_code}
      globalSearch(first:1,input:"ABC"){case_count}
    }"""
    result = validate_graphql_query(catalog, too_many_data_roots)
    assert not result["valid"]
    assert "at most 3 data root fields are allowed" in result["errors"]


def test_study_detail_and_search_require_meaningful_inputs(catalog):
    detail = """query Detail($first:Int!,$code:String!){
      studyDetail(first:$first,study_code:$code){study_code}
    }"""
    assert validate_graphql_query(catalog, detail, {"first": 1, "code": "ABC"})["valid"]
    assert not validate_graphql_query(catalog, detail, {"first": 1, "code": "  "})["valid"]
    assert not validate_graphql_query(catalog, detail, {"first": 1})["valid"]
    search = """query Search($first:Int!,$input:String!){
      globalSearch(first:$first,input:$input){case_count}
    }"""
    assert validate_graphql_query(catalog, search, {"first": 1, "input": "lymphoma"})["valid"]
    assert not validate_graphql_query(catalog, search, {"first": 1, "input": " "})["valid"]
    assert not validate_graphql_query(catalog, search, {"first": 1, "input": "x" * 101})["valid"]


def test_nested_pagination_is_bounded(catalog):
    query = """query Links($first:Int,$links:Int,$offset:Int){
      studiesByProgram(first:$first){
        clinical_study_name CRDCLinks(first:$links,offset:$offset){url}
      }
    }"""
    assert validate_graphql_query(
        catalog, query, {"first": 2, "links": 3, "offset": 0},
    )["valid"]
    assert not validate_graphql_query(
        catalog, query, {"first": 2, "links": 1_000_000, "offset": 0},
    )["valid"]
    assert not validate_graphql_query(
        catalog, query, {"first": 2, "links": 3, "offset": 10_001},
    )["valid"]
    assert not validate_graphql_query(
        catalog, query, {"first": 2, "offset": 0},
    )["valid"]


@pytest.mark.parametrize("query", [
    "mutation Write {createManifest}",
    "query Inspect {__schema{queryType{name}}}",
    "{__typename}",
    "query Bulk {filesInList{file_id}}",
    "query Frag {caseOverview(first:1){...Fields}} fragment Fields on CaseOverviewES {case_id}",
])
def test_disallowed_operations_and_expansion(catalog, query):
    assert not validate_graphql_query(catalog, query)["valid"]


def test_variable_limits_and_response_metadata(catalog):
    query = "query Counts {numberOfStudies}"
    assert not validate_graphql_query(catalog, query, {"input": "x" * 513})["valid"]
    assert not validate_graphql_query(catalog, query, {"input": ["x"] * 101})["valid"]
    assert not validate_graphql_query(catalog, query, {"input": float("nan")})["valid"]
    assert not validate_graphql_query(catalog, "x" * 8_001)["valid"]
    validated = validate_graphql_query(catalog, query)
    assert validated["source"] == catalog.source()
    assert "pv_checks" not in validated


@pytest.mark.asyncio
async def test_upstream_posts_once_to_fixed_host_and_preserves_partial_errors():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": {"numberOfStudies": 1}, "errors": [{"message": "partial"}]})

    result = await fetch_graphql(
        "query Counts {numberOfStudies}", {}, "Counts", transport=httpx.MockTransport(respond),
    )
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == GRAPHQL_URL
    assert json.loads(requests[0].content) == {
        "query": "query Counts {numberOfStudies}", "variables": {}, "operationName": "Counts",
    }
    assert result["payload"] == {"data": {"numberOfStudies": 1}, "errors": [{"message": "partial"}]}
    assert result["http_status"] == 200


@pytest.mark.asyncio
async def test_upstream_rejects_redirect_oversize_invalid_json_and_timeout():
    query = "query Counts {numberOfStudies}"
    cases = [
        (lambda _: httpx.Response(302, headers={"Location": "https://other.example/"}), "redirect_rejected"),
        (lambda _: httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1)), "response_too_large"),
        (lambda _: httpx.Response(200, content=b"not-json"), "invalid_upstream_json"),
        (lambda _: httpx.Response(200, json=[1, 2]), "invalid_upstream_json"),
    ]
    for handler, code in cases:
        with pytest.raises(UpstreamFailure) as raised:
            await fetch_graphql(query, {}, "Counts", transport=httpx.MockTransport(handler))
        assert raised.value.code == code

    def timeout(_):
        raise httpx.ReadTimeout("mock timeout")

    with pytest.raises(UpstreamFailure) as raised:
        await fetch_graphql(query, {}, "Counts", transport=httpx.MockTransport(timeout))
    assert raised.value.code == "upstream_timeout"

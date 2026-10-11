import json

import httpx
import pytest

from popsci_mcp.catalog import Catalog, GRAPHQL_URL
from popsci_mcp.query import ALLOWED_ROOTS, UpstreamFailure, fetch_graphql, validate_graphql_query


@pytest.fixture(scope="module")
def catalog():
    return Catalog()


def test_bundled_schema_and_exact_model_values(catalog):
    assert len(catalog.schema.query_type.fields) == 74
    assert len(catalog.properties) == 58
    assert ALLOWED_ROOTS <= set(catalog.schema.query_type.fields)
    assert catalog.source()["model"] == "popsci"
    assert catalog.source()["model_url"].endswith("popsci-model-props.yml")
    assert catalog.list_permissible_values("study_design")["total"] == 13
    assert catalog.check_permissible_value("study_design", "Clinical Trial")["listed"]
    assert not catalog.check_permissible_value("study_design", "clinical trial")["listed"]
    assert catalog.list_permissible_values("study_name")["error"] == "no_enum"
    assert catalog.list_permissible_values("missing")["error"] == "unknown_property"
    assert catalog.describe_query_field("tabStudy")["named_return_type"] == "TabStudy"


def test_study_queries_and_pv_checks_are_advisory(catalog):
    query = "query Q($first:Int!, $study_design:[String]) { tabStudy(first:$first, study_design:$study_design) { study_short_name study_design } }"
    checked = validate_graphql_query(catalog, query, {"first": 1, "study_design": ["Clinical Trial"]})
    assert checked["valid"]
    assert checked["pv_checks"][0]["all_listed"] is True
    assert checked["pv_checks"][0]["advisory_only"] is True
    unlisted = validate_graphql_query(catalog, query, {"first": 1, "study_design": ["clinical trial"]})
    assert unlisted["valid"]
    assert unlisted["pv_checks"][0]["unlisted_values"] == ["clinical trial"]
    assert validate_graphql_query(catalog, "{ searchStudies { numberOfStudies studyCountByStudyDesign { group subjects } } }")["valid"]
    assert validate_graphql_query(catalog, '{ studyDemographics(first: 1, study_short_name: ["NLST"]) { participant_races { group subjects } } }')["valid"]
    assert validate_graphql_query(catalog, '{ dataCollectionPage(study_short_name: ["NLST"]) { data_collection { data_collection_category } } }')["valid"]


@pytest.mark.parametrize("query", [
    '{ tabStudy { study_name } }',
    '{ tabStudy(first: 101) { study_name } }',
    '{ tabStudy(first: 1, offset: 10001) { study_name } }',
    '{ study_subject(first: 1) { study_subject_first_name } }',
    '{ idsLists(first: 1) { subjectIds } }',
    '{ studyFiles(first: 1) { data_file_signed_url } }',
    '{ studyFiles(first: 1) { x:data_file_location } }',
    '{ tabStudy(first: 1) { personnel { email_address } } }',
    '{ studyGeneral { study_short_name } }',
    '{ globalStatsBar { number_of_participants } }',
    '{ studyDemographics(first: 1) { study_name } }',
    '{ tabStudy(first: 1, study_name_matches: ".*") { study_name } }',
    '{ __schema { queryType { name } } }',
    'mutation { anything }',
])
def test_rejected_queries(catalog, query):
    assert not validate_graphql_query(catalog, query)["valid"]


@pytest.mark.asyncio
async def test_upstream_one_post_and_error_preservation():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": {"tabStudy": []}, "errors": [{"message": "partial"}]})

    result = await fetch_graphql('{ tabStudy(first: 1) { study_name } }', transport=httpx.MockTransport(respond))
    assert len(requests) == 1
    assert str(requests[0].url) == GRAPHQL_URL
    assert json.loads(requests[0].content)["variables"] == {}
    assert result["payload"]["errors"][0]["message"] == "partial"


@pytest.mark.asyncio
async def test_upstream_redirect_and_response_limit():
    with pytest.raises(UpstreamFailure, match="redirect_rejected"):
        await fetch_graphql('{ tabStudy(first: 1) { study_name } }', transport=httpx.MockTransport(
            lambda _r: httpx.Response(302, headers={"Location": "https://other.example/"})))
    with pytest.raises(UpstreamFailure, match="response_too_large"):
        await fetch_graphql('{ tabStudy(first: 1) { study_name } }', transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, content=b"x" * 2_000_001)))

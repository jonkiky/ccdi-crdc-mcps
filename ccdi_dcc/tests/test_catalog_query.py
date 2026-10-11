import json

import httpx
import pytest

from ccdi_dcc_mcp.catalog import Catalog, GRAPHQL_URL
from ccdi_dcc_mcp.query import ALLOWED_ROOTS, UpstreamFailure, fetch_graphql, validate_graphql_query


@pytest.fixture(scope="module")
def catalog():
    return Catalog()


def test_schema_and_pv_source(catalog):
    assert len(catalog.schema.query_type.fields) == 216
    assert ALLOWED_ROOTS <= set(catalog.schema.query_type.fields)
    assert catalog.describe_query_field("participantsConnection")["named_return_type"] == "ParticipantsConnection"
    assert any(field["name"] == "sex_at_birth_EQ" for field in catalog.describe_type("ParticipantWhere")["fields"])
    assert catalog.check_permissible_value("sex_at_birth", "Don't Know")["listed"]
    assert not catalog.check_permissible_value("sex_at_birth", "Don't know")["listed"]
    assert catalog.list_permissible_values("diagnosis", limit=200)["total"] == 540
    assert catalog.list_permissible_values("race")["error"] == "no_enum"
    assert catalog.list_permissible_values("missing")["error"] == "unknown_property"
    assert catalog.source()["model_url"].endswith("ccdi-model-props.yml")


def test_bounded_validation_and_exact_pv_checks(catalog):
    valid = validate_graphql_query(
        catalog,
        "query Q($where: ParticipantWhere, $first: Int!) { participantsConnection(where: $where, first: $first) { totalCount edges { node { participant_id } } } }",
        {"where": {"sex_at_birth_EQ": "Don't know"}, "first": 2},
    )
    assert valid["valid"]
    assert valid["pv_checks"][0]["all_listed"] is False
    assert valid["pv_checks"][0]["advisory_only"] is True
    assert validate_graphql_query(catalog, '{ participants(limit: 1, where: {sex_at_birth_IN: ["Female"]}) { guid } }')["pv_checks"][0]["all_listed"]
    assert validate_graphql_query(catalog, '{ participantsConnection(first: 1) { totalCount } }')["valid"]
    assert validate_graphql_query(catalog, '{ studies(limit: 1) { guid study_name } }')["valid"]
    assert validate_graphql_query(catalog, '{ studiesAggregate { count } }')["valid"]


@pytest.mark.parametrize("query", [
    '{ studies { guid } }',
    '{ studies(limit: 101) { guid } }',
    '{ studies(limit: 1, offset: 10001) { guid } }',
    '{ participantsConnection { totalCount } }',
    '{ participantsConnection(first: 101) { totalCount } }',
    '{ studies(limit: 1, options: {limit: 5}) { guid } }',
    '{ sampleOfSampleParticipantEdges(limit: 1) { id } }',
    '{ memgraphNodes(limit: 1) { guid } }',
    '{ studies(limit: 1) { in_of_cell_line_from_cell_line_edges { from { guid } } } }',
    '{ __schema { queryType { name } } }',
    'mutation { anything }',
])
def test_rejected_queries(catalog, query):
    assert not validate_graphql_query(catalog, query)["valid"]


@pytest.mark.asyncio
async def test_upstream_one_post_preserves_graphql_errors():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": {"studies": []}, "errors": [{"message": "partial"}]})

    result = await fetch_graphql('{ studies(limit: 1) { guid } }', transport=httpx.MockTransport(respond))
    assert len(requests) == 1
    assert str(requests[0].url) == GRAPHQL_URL
    assert json.loads(requests[0].content)["variables"] == {}
    assert result["payload"]["errors"][0]["message"] == "partial"


@pytest.mark.asyncio
async def test_upstream_redirect_and_response_limit():
    with pytest.raises(UpstreamFailure, match="redirect_rejected"):
        await fetch_graphql('{ studies(limit: 1) { guid } }', transport=httpx.MockTransport(
            lambda _r: httpx.Response(302, headers={"Location": "https://other.example/"})))
    with pytest.raises(UpstreamFailure, match="response_too_large"):
        await fetch_graphql('{ studies(limit: 1) { guid } }', transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, content=b"x" * 2_000_001)))

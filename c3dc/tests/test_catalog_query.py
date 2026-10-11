import json

import httpx
import pytest

from c3dc_mcp.catalog import Catalog, GRAPHQL_URL
from c3dc_mcp.query import UpstreamFailure, fetch_graphql, validate_graphql_query


@pytest.fixture(scope="module")
def catalog():
    return Catalog()


def test_model_enums_preserve_exact_values_and_provenance(catalog):
    ccdi = catalog.list_permissible_values("sex_at_birth")
    assert "Don't Know" in ccdi["values"]
    assert "Don't know" not in ccdi["values"]
    assert ccdi["source"]["model"] == "ccdi"
    assert ccdi["source"]["model_url"].endswith("ccdi-model-props.yml")
    assert catalog.check_permissible_value("sex_at_birth", "Don't Know")["listed"]
    assert not catalog.check_permissible_value("sex_at_birth", "Don't know")["listed"]


def test_large_enum_pages_are_complete_and_no_enum_is_distinct(catalog):
    first = catalog.list_permissible_values("diagnosis", limit=200)
    second = catalog.list_permissible_values("diagnosis", offset=200, limit=200)
    third = catalog.list_permissible_values("diagnosis", offset=400, limit=200)
    assert first["total"] == 540
    assert len(first["values"] + second["values"] + third["values"]) == 540
    assert third["next_offset"] is None
    assert catalog.list_permissible_values("race")["error"] == "no_enum"
    assert catalog.list_permissible_values("missing")["error"] == "unknown_property"


def test_query_validation_limits_scope_and_checks_pvs(catalog):
    good = validate_graphql_query(
        catalog,
        "query Q($first:Int!, $offset:Int!){studiesListing(first:$first,offset:$offset){study_id study_name}}",
        {"first": 2, "offset": 0},
    )
    assert good["valid"]
    assert not validate_graphql_query(catalog, "{studiesListing{study_id}}")["valid"]
    assert not validate_graphql_query(catalog, "{studiesListing(first:101){study_id}}")["valid"]
    assert not validate_graphql_query(catalog, "{idsLists{participantIds}}")["valid"]
    assert not validate_graphql_query(catalog, "{__schema{queryType{name}}}")["valid"]
    assert not validate_graphql_query(catalog, "mutation{something}")["valid"]
    checked = validate_graphql_query(
        catalog,
        "query Q($sex_at_birth:[String]){searchParticipants(sex_at_birth:$sex_at_birth){numberOfParticipants}}",
        {"sex_at_birth": ["Don't know"]},
    )
    assert checked["valid"]
    assert checked["pv_checks"][0]["all_listed"] is False
    assert checked["pv_checks"][0]["advisory_only"] is True
    listed = validate_graphql_query(
        catalog,
        "query Q($sex_at_birth:[String]){searchParticipants(sex_at_birth:$sex_at_birth){numberOfParticipants}}",
        {"sex_at_birth": ["Don't Know"]},
    )
    assert listed["pv_checks"][0]["all_listed"] is True


@pytest.mark.asyncio
async def test_upstream_one_post_preserves_data_and_errors():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": {"numberOfParticipants": 5}, "errors": [{"message": "partial"}]})

    result = await fetch_graphql(
        "{numberOfParticipants}", transport=httpx.MockTransport(respond),
    )
    assert len(requests) == 1
    assert str(requests[0].url) == GRAPHQL_URL
    assert json.loads(requests[0].content)["variables"] == {}
    assert result["payload"]["data"]["numberOfParticipants"] == 5
    assert result["payload"]["errors"][0]["message"] == "partial"


@pytest.mark.asyncio
async def test_upstream_rejects_redirect_and_oversize():
    with pytest.raises(UpstreamFailure, match="redirect_rejected"):
        await fetch_graphql(
            "{numberOfParticipants}",
            transport=httpx.MockTransport(lambda _r: httpx.Response(302, headers={"Location": "https://other.example/"})),
        )
    with pytest.raises(UpstreamFailure, match="response_too_large"):
        await fetch_graphql(
            "{numberOfParticipants}",
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=b"x" * 2_000_001)),
        )

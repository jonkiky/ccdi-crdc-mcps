import json

import httpx
import pytest

from ctdc_mcp.catalog import Catalog, GRAPHQL_URL
from ctdc_mcp.query import UpstreamFailure, fetch_graphql, validate_graphql_query


@pytest.fixture(scope="module")
def catalog():
    return Catalog()


def test_schema_discovery_and_source(catalog):
    fields = catalog.list_query_fields()
    assert fields["total"] >= 20
    assert any(field["name"] == "participantOverview" for field in fields["fields"])
    overview = catalog.describe_query_field("participantOverview")
    assert {arg["name"] for arg in overview["arguments"]} >= {"first", "offset", "study_short_name"}
    assert overview["named_return_type"] == "ParticipantOverview"
    assert any(field["name"] == "participant_id" for field in catalog.describe_type("ParticipantOverview")["fields"])
    assert fields["source"]["graphql_endpoint"] == GRAPHQL_URL
    assert "model" not in fields["source"]


def test_query_validation_bounds_read_only_metadata(catalog):
    query = """query Participants($first:Int,$offset:Int){
      participantOverview(first:$first,offset:$offset){participant_id study_short_name}
    }"""
    good = validate_graphql_query(catalog, query, {"first": 2, "offset": 0})
    assert good["valid"]
    assert good["operation_name"] == "Participants"
    assert validate_graphql_query(catalog, "{getHomePage{numberOfParticipants}}") ["valid"]
    assert not validate_graphql_query(catalog, query, {"first": 101, "offset": 0})["valid"]
    assert not validate_graphql_query(catalog, query, {"first": 2, "offset": -1})["valid"]
    assert not validate_graphql_query(catalog, "query Inspect {__schema{queryType{name}}}")["valid"]
    assert not validate_graphql_query(catalog, "mutation Mutate {something}")["valid"]
    assert not validate_graphql_query(catalog, "query Bulk {fileIDsFromList}")["valid"]
    assert not validate_graphql_query(catalog, "query Study {studyByStudyShortName{study_name}}")["valid"]
    assert not validate_graphql_query(catalog, "query Clinical {clinicalData{study_short_name}}")["valid"]
    assert validate_graphql_query(
        catalog, "query Study {studyByStudyShortName(study_short_name:[\"CMB\"]){study_name}}",
    )["valid"]


@pytest.mark.asyncio
async def test_upstream_one_post_preserves_payload_and_operation_name():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": {"getHomePage": []}, "errors": [{"message": "partial"}]})

    result = await fetch_graphql(
        "query Home {getHomePage{numberOfParticipants}}", {}, "Home",
        transport=httpx.MockTransport(respond),
    )
    assert len(requests) == 1
    assert str(requests[0].url) == GRAPHQL_URL
    assert json.loads(requests[0].content)["operationName"] == "Home"
    assert result["payload"]["errors"][0]["message"] == "partial"


@pytest.mark.asyncio
async def test_upstream_rejects_redirect_and_oversize():
    with pytest.raises(UpstreamFailure, match="redirect_rejected"):
        await fetch_graphql(
            "query Home {getHomePage{numberOfParticipants}}", {}, "Home",
            transport=httpx.MockTransport(lambda _r: httpx.Response(302, headers={"Location": "https://other.example/"})),
        )
    with pytest.raises(UpstreamFailure, match="response_too_large"):
        await fetch_graphql(
            "query Home {getHomePage{numberOfParticipants}}", {}, "Home",
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=b"x" * 2_000_001)),
        )

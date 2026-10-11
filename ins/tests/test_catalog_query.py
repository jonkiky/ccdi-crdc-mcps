import json

import httpx
import pytest

from ins_mcp.catalog import Catalog, GRAPHQL_URL
from ins_mcp.query import UpstreamFailure, fetch_graphql, validate_graphql_query


@pytest.fixture(scope="module")
def catalog():
    return Catalog()


def test_schema_discovery_and_provenance(catalog):
    fields = catalog.list_query_fields()
    assert fields["total"] > 50
    assert any(field["name"] == "projectsOverview" for field in fields["fields"])
    overview = catalog.describe_query_field("projectsOverview")
    assert {arg["name"] for arg in overview["arguments"]} >= {"first", "offset", "program_ids"}
    assert overview["named_return_type"] == "ProjectsOverview"
    assert any(field["name"] == "project_title" for field in catalog.describe_type("ProjectsOverview")["fields"])
    assert fields["source"]["graphql_endpoint"] == GRAPHQL_URL
    assert "model" not in fields["source"]


def test_query_validation_requires_named_bounded_read_only_operations(catalog):
    query = """query Projects($first:Int,$offset:Int){
      projectsOverview(first:$first,offset:$offset){project_id project_title}
    }"""
    good = validate_graphql_query(catalog, query, {"first": 2, "offset": 0})
    assert good["valid"]
    assert good["operation_name"] == "Projects"
    assert not validate_graphql_query(catalog, "{numberOfProjects}")["valid"]
    assert not validate_graphql_query(catalog, query, {"first": 101, "offset": 0})["valid"]
    assert not validate_graphql_query(catalog, query, {"first": 2, "offset": -1})["valid"]
    assert not validate_graphql_query(catalog, "query Mutate {__schema{queryType{name}}}")["valid"]
    assert not validate_graphql_query(catalog, "mutation Mutate {something}")["valid"]
    assert not validate_graphql_query(catalog, "query Bulk {projectsInList {project_id}}")["valid"]
    assert not validate_graphql_query(catalog, "query Missing {projectDetails {project_id}}")["valid"]


@pytest.mark.asyncio
async def test_upstream_one_post_includes_operation_name_and_preserves_errors():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": {"numberOfProjects": 7}, "errors": [{"message": "partial"}]})

    result = await fetch_graphql(
        "query Count {numberOfProjects}", {}, "Count", transport=httpx.MockTransport(respond),
    )
    assert len(requests) == 1
    assert str(requests[0].url) == GRAPHQL_URL
    assert json.loads(requests[0].content)["operationName"] == "Count"
    assert result["payload"]["data"]["numberOfProjects"] == 7
    assert result["payload"]["errors"][0]["message"] == "partial"


@pytest.mark.asyncio
async def test_upstream_rejects_redirect_and_oversize():
    with pytest.raises(UpstreamFailure, match="redirect_rejected"):
        await fetch_graphql(
            "query Count {numberOfProjects}", {}, "Count",
            transport=httpx.MockTransport(lambda _r: httpx.Response(302, headers={"Location": "https://other.example/"})),
        )
    with pytest.raises(UpstreamFailure, match="response_too_large"):
        await fetch_graphql(
            "query Count {numberOfProjects}", {}, "Count",
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=b"x" * 2_000_001)),
        )

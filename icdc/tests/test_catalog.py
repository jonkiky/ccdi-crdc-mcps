"""Offline checks for the checked-in public ICDC GraphQL schema."""

import hashlib

import pytest

from icdc_mcp.catalog import ASSETS, Catalog, GRAPHQL_URL, SCHEMA_CAPTURED


@pytest.fixture(scope="module")
def catalog():
    return Catalog()


def test_schema_source_and_known_icdc_fields(catalog):
    source = catalog.source()
    assert source == {
        "graphql_endpoint": GRAPHQL_URL,
        "graphql_schema_sha256": hashlib.sha256(
            (ASSETS / "graphql-introspection.json").read_bytes(),
        ).hexdigest(),
        "schema_captured": SCHEMA_CAPTURED,
    }
    assert source["graphql_endpoint"] == "https://caninecommons.cancer.gov/v1/graphql/"
    assert source["schema_captured"] == "2026-10-11"
    assert catalog.schema.query_type.name == "QueryType"
    assert {"studiesByProgram", "caseOverview", "globalSearch", "createManifest"} <= set(
        catalog.schema.query_type.fields
    )


def test_discovery_pages_every_root_and_describes_arguments(catalog):
    first = catalog.list_query_fields(limit=100)
    second = catalog.list_query_fields(offset=first["next_offset"], limit=100)
    all_names = [item["name"] for item in first["fields"] + second["fields"]]
    assert len(all_names) == first["total"] == 125
    assert all_names == sorted(set(all_names))
    assert second["next_offset"] is None
    assert first["source"] == catalog.source()

    study = catalog.describe_query_field("studiesByProgram")
    assert study["named_return_type"] == "StudyOfProgram"
    assert {arg["name"] for arg in study["arguments"]} >= {"first", "offset"}
    assert study["source"] == catalog.source()
    fields = catalog.describe_type("StudyOfProgram")["fields"]
    assert {"program_id", "clinical_study_designation", "numberOfCases"} <= {
        field["name"] for field in fields
    }


def test_discovery_rejects_invalid_pages_and_unknown_names(catalog):
    assert catalog.list_query_fields(offset=-1)["error"] == "invalid_page"
    assert catalog.list_query_fields(limit=101)["error"] == "invalid_page"
    assert catalog.list_query_fields(limit=True)["error"] == "invalid_page"
    assert catalog.describe_type("CaseOverviewES", limit=0)["error"] == "invalid_page"
    assert catalog.describe_query_field("not_a_root")["error"] == "unknown_query_field"
    assert catalog.describe_type("not_a_type")["error"] == "unknown_type"
    assert catalog.describe_type("__Schema")["error"] == "unknown_type"


def test_rejects_malformed_schema_asset(tmp_path):
    (tmp_path / "graphql-introspection.json").write_text('{"data": {}}')
    with pytest.raises(ValueError, match="invalid ICDC GraphQL introspection asset"):
        Catalog(tmp_path)

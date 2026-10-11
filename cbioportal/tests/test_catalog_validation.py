import json

import pytest

from cbioportal_mcp.catalog import API_ORIGIN, Catalog
from cbioportal_mcp.validation import validate_request


@pytest.fixture(scope="module")
def catalog():
    return Catalog()


def test_pinned_catalog_contains_only_documented_reads(catalog):
    assert len(catalog.operations) == 62
    assert sum(op["method"] == "GET" for op in catalog.operations.values()) == 39
    assert sum(op["method"] == "POST" for op in catalog.operations.values()) == 23
    assert all(op["path"].endswith("/fetch") for op in catalog.operations.values() if op["method"] == "POST")
    assert catalog.list_operations()["total"] == 62
    assert catalog.list_operations(method="POST")["total"] == 23
    assert catalog.describe_operation("getAllStudiesUsingGET")["path"] == "/api/studies"
    assert catalog.describe_operation("missing")["error"] == "operation_not_found"
    assert catalog.describe_schema("SampleFilter")["schema"]["type"] == "object"
    assert catalog.describe_schema("missing")["error"] == "schema_not_found"
    assert not any("permissible" in name.lower() for name in catalog.spec)


def test_operation_filters_and_paging(catalog):
    first = catalog.list_operations(tag="Studies", limit=2)
    second = catalog.list_operations(tag="Studies", offset=first["next_offset"], limit=2)
    assert len(first["operations"]) == len(second["operations"]) == 2
    assert first["operations"] != second["operations"]
    assert catalog.list_operations(tag="missing")["error"] == "invalid_tag"
    assert catalog.list_operations(method="DELETE")["error"] == "invalid_method"
    assert catalog.list_operations(limit=101)["error"] == "invalid_pagination"


def test_openapi_origin_is_pinned(tmp_path, catalog):
    spec = dict(catalog.spec)
    spec["servers"] = [{"url": "https://example.com"}]
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="origin"):
        Catalog(path)
    assert API_ORIGIN == "https://cbioportal-api.ccdi.cancer.gov"


def test_get_validation_uses_zero_based_bounded_pagination(catalog):
    result = validate_request(catalog, "getAllStudiesUsingGET")
    assert result["valid"]
    assert result["request"]["query_params"] == {"pageSize": 20, "pageNumber": 0}
    assert validate_request(catalog, "getAllStudiesUsingGET", query_params={"pageSize": 200})["valid"]
    for size in (0, 201, True, "20"):
        assert not validate_request(catalog, "getAllStudiesUsingGET", query_params={"pageSize": size})["valid"]
    assert not validate_request(catalog, "getAllStudiesUsingGET", query_params={"pageNumber": -1})["valid"]


def test_query_contract_and_uncontrolled_values(catalog):
    assert validate_request(catalog, "getAllStudiesUsingGET", query_params={"keyword": "unlisted value"})["valid"]
    assert not validate_request(catalog, "getAllStudiesUsingGET", query_params={"projection": "WRONG"})["valid"]
    assert not validate_request(catalog, "getAllStudiesUsingGET", query_params={"unknown": "x"})["valid"]
    assert not validate_request(catalog, "getAllStudiesUsingGET", body={})["valid"]
    missing = validate_request(catalog, "getMutationsInMolecularProfileBySampleListIdUsingGET", {"molecularProfileId": "x"})
    assert {item["code"] for item in missing["errors"]} == {"missing_query_parameter"}


def test_safe_path_and_post_body(catalog):
    result = validate_request(catalog, "getStudyUsingGET", {"studyId": "a b+c"})
    assert result["request"]["path"] == "/api/studies/a%20b%2Bc"
    for bad in ("../secret", "x/y", "%2fsecret", "x?y", "x#y", "x\\y"):
        assert not validate_request(catalog, "getStudyUsingGET", {"studyId": bad})["valid"]
    assert not validate_request(catalog, "fetchStudiesUsingPOST")["valid"]
    assert validate_request(catalog, "fetchStudiesUsingPOST", body=["study_id"])["valid"]
    assert not validate_request(catalog, "fetchStudiesUsingPOST", body={"studyId": "x"})["valid"]
    assert not validate_request(catalog, "fetchStudiesUsingPOST", body=["x" * 33000])["valid"]


def test_nested_post_body_schema(catalog):
    args = {"molecularProfileId": "profile_1"}
    valid = validate_request(catalog, "fetchMutationsInMolecularProfileUsingPOST", args, body={"sampleIds": ["sample_1"]})
    assert valid["valid"]
    assert valid["request"]["path"] == "/api/molecular-profiles/profile_1/mutations/fetch"
    invalid = validate_request(catalog, "fetchMutationsInMolecularProfileUsingPOST", args, body={"sampleIds": [4]})
    assert invalid["errors"][0]["code"] == "invalid_body"

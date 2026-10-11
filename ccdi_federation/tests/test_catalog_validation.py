import json
import shutil

import pytest

from ccdi_federation_mcp.catalog import Catalog
from ccdi_federation_mcp.validation import validate_query


@pytest.fixture(scope="module")
def catalog():
    return Catalog()


def test_catalog_has_all_documented_get_operations(catalog):
    assert set(catalog.operations) == {
        "subject_index", "subject_show", "subjects_by_count", "subject_summary", "subject_cpi",
        "sample_index", "sample_show", "samples_by_count", "sample_summary",
        "file_index", "file_show", "files_by_count", "file_summary",
        "metadata_fields_subject", "metadata_fields_sample", "metadata_fields_file",
        "namespace_index", "namespace_show", "organization_index", "organization_show",
        "info_index", "sample_diagnosis_index", "subject_diagnosis_index",
    }
    assert catalog.list_operations()["total"] == 23
    assert all(op["method"] == "GET" for op in catalog.operations.values())
    assert catalog.describe_operation("sample_index")["path"] == "/sample"
    assert catalog.describe_operation("missing")["error"] == "operation_not_found"
    assert {p["name"] for p in catalog.describe_operation("subject_index")["parameters"]} >= {
        "sex", "race", "page", "per_page", "metadata.unharmonized.<field>"
    }


@pytest.mark.parametrize("entity,total", [("subject", 22), ("sample", 128), ("file", 80)])
def test_exact_pv_enumeration_across_pages(catalog, entity, total):
    entries = []
    offset = 0
    while True:
        page = catalog.list_permissible_values(entity, offset=offset, limit=17)
        entries.extend(page["values"])
        assert page["total"] == total
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert len(entries) == total
    assert len({(item["field"], item["value"]) for item in entries}) == total
    assert entries == [
        {"field": field, **value}
        for field in sorted(catalog.pv[entity])
        for value in (catalog.pv[entity][field]["permissible_values"] or [])
    ]


def test_pv_field_states_and_exact_values(catalog):
    sex = catalog.list_permissible_values("subject", "sex")
    assert [item["value"] for item in sex["values"]] == [
        item["value"] for item in catalog.pv["subject"]["sex"]["permissible_values"]
    ]
    assert catalog.get_field_metadata("sample", "diagnosis")["controlled"] is False
    assert catalog.get_field_metadata("sample", "missing")["error"] == "field_not_found"
    assert catalog.list_permissible_values("sample", "diagnosis")["error"] == "no_bundled_pvs"
    assert catalog.list_permissible_values("other")["error"] == "unsupported_entity"
    assert catalog.list_permissible_values("file", limit=201)["error"] == "invalid_pagination"


def test_unmatched_pv_value_is_not_rewritten_or_rejected(catalog):
    result = validate_query(catalog, "subject_index", query_params={"sex": "Female"})
    assert result["valid"]
    assert result["request"]["query_params"] == {"sex": "Female", "page": 1, "per_page": 20}
    assert not any(item["value"] == "Female" for item in catalog.list_permissible_values("subject", "sex")["values"])


@pytest.mark.parametrize("per_page,valid", [(1, True), (20, True), (200, True), (0, False), (201, False), (True, False)])
def test_per_page_bounds(catalog, per_page, valid):
    assert validate_query(catalog, "file_index", query_params={"per_page": per_page})["valid"] is valid


def test_page_must_be_positive_integer(catalog):
    for page in (0, -1, True, "1"):
        assert not validate_query(catalog, "file_index", query_params={"page": page})["valid"]


def test_undocumented_query_filters_are_rejected(catalog):
    assert not validate_query(catalog, "sample_index", query_params={"diagnosis_category": "x"})["valid"]
    assert not validate_query(catalog, "subject_summary", query_params={"per_page": 20})["valid"]
    assert validate_query(catalog, "sample_diagnosis_index", query_params={"search": "leukemia"})["valid"]
    assert not validate_query(catalog, "sample_index", query_params={"search": "leukemia"})["valid"]


def test_unharmonized_field_only_on_documented_operations(catalog):
    key = "metadata.unharmonized.test_field"
    assert validate_query(catalog, "sample_index", query_params={key: "x"})["valid"]
    path = {"organization": "x", "namespace": "y", "name": "z"}
    assert not validate_query(catalog, "sample_show", path, {key: "x"})["valid"]
    assert not validate_query(catalog, "sample_index", query_params={"metadata.unharmonized.<field>": "x"})["valid"]
    assert not validate_query(catalog, "sample_index", query_params={"metadata.unharmonized.": "x"})["valid"]


def test_count_field_and_path_validation(catalog):
    assert validate_query(catalog, "samples_by_count", path_params={"field": "diagnosis_category"})["valid"]
    assert not validate_query(catalog, "samples_by_count", path_params={"field": "unknown"})["valid"]
    args = {"organization": "x y", "namespace": "ns", "name": "a+b"}
    assert validate_query(catalog, "sample_show", path_params=args)["request"]["path"] == "/sample/x%20y/ns/a%2Bb"
    for bad in ("../x", "x/y", "%2fsecret", "x?y", "x#y", "x\\y"):
        args["organization"] = bad
        assert not validate_query(catalog, "sample_show", path_params=args)["valid"]
    assert not validate_query(catalog, "sample_show", path_params={"organization": "x"})["valid"]


def test_missing_assets_fail_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        Catalog(tmp_path)


def test_malformed_pv_asset_fails_closed(tmp_path, catalog):
    shutil.copytree(catalog.reference_dir, tmp_path / "references")
    path = tmp_path / "references/pv/subject-pv-metadata.json"
    data = json.loads(path.read_text())
    data["sex"]["permissible_values"] = "invalid"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="invalid subject PV asset"):
        Catalog(tmp_path / "references")


def test_pv_resource_payload_is_exact(catalog):
    # Catalog data is JSON-serializable without any semantic value mapping.
    json.dumps(catalog.list_permissible_values("sample", "diagnosis_category"))

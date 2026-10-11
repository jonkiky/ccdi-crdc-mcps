"""Validate one documented metadata GET without consulting PV values."""

from __future__ import annotations

import re
from urllib.parse import quote

from .catalog import Catalog


UNHARMONIZED_PREFIX = "metadata.unharmonized."
UNHARMONIZED_PLACEHOLDER = UNHARMONIZED_PREFIX + "<field>"
FIELD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def _valid_segment(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 256:
        return False
    if value in (".", "..") or any(c in value for c in "/\\?#"):
        return False
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return False
    return not re.search(r"%(?:2e|2f|5c|3f|23)", value, re.IGNORECASE)


def _valid_query_value(value: object, schema: dict) -> bool:
    kind = schema.get("type", "string")
    if kind == "integer":
        return type(value) is int and value >= schema.get("minimum", 0)
    if kind == "string":
        return isinstance(value, str) and len(value) <= 2048 and not any(
            ord(c) < 32 or ord(c) == 127 for c in value
        )
    return False


def validate_query(
    catalog: Catalog,
    operation_id: str,
    path_params: dict | None = None,
    query_params: dict | None = None,
) -> dict:
    errors: list[dict] = []
    op = catalog.operations.get(operation_id)
    if op is None:
        return {
            "valid": False, "executable": False, "request": None,
            "errors": [{"code": "operation_not_found", "operation_id": operation_id}],
            "warnings": [], "source": catalog.source,
        }
    if not isinstance(path_params, (dict, type(None))) or not isinstance(query_params, (dict, type(None))):
        return {
            "valid": False, "executable": False, "request": None,
            "errors": [{"code": "invalid_parameter_object"}],
            "warnings": [], "source": catalog.source,
        }
    path_params = path_params or {}
    query_params = query_params or {}
    if len(query_params) > 32:
        errors.append({"code": "too_many_query_parameters"})
    path_specs = {p["name"]: p for p in op["parameters"] if p["in"] == "path"}
    query_specs = {p["name"]: p for p in op["parameters"] if p["in"] == "query"}
    for name in sorted(path_specs.keys() - path_params.keys()):
        errors.append({"code": "missing_path_parameter", "parameter": name})
    for name in sorted(path_params.keys() - path_specs.keys()):
        errors.append({"code": "unknown_path_parameter", "parameter": name})
    for name, value in path_params.items():
        if name in path_specs and not _valid_segment(value):
            errors.append({"code": "invalid_path_parameter", "parameter": name})
    if "field" in path_specs and "field" in path_params and _valid_segment(path_params["field"]):
        entity = op["path"].split("/")[1]
        if entity in catalog.pv and path_params["field"] not in catalog.pv[entity]:
            errors.append({"code": "unverified_count_field", "parameter": "field"})

    normalized_query = dict(query_params)
    for name, value in query_params.items():
        if not isinstance(name, str):
            errors.append({"code": "invalid_query_name"})
            continue
        if name == UNHARMONIZED_PLACEHOLDER:
            errors.append({"code": "unresolved_query_placeholder", "parameter": name})
            continue
        spec = query_specs.get(name)
        if spec is None and name.startswith(UNHARMONIZED_PREFIX):
            suffix = name[len(UNHARMONIZED_PREFIX):]
            if (
                UNHARMONIZED_PLACEHOLDER in query_specs
                and len(suffix) <= 128
                and FIELD_NAME.fullmatch(suffix)
            ):
                spec = query_specs[UNHARMONIZED_PLACEHOLDER]
        if spec is None:
            errors.append({"code": "unknown_query_parameter", "parameter": name})
        elif not _valid_query_value(value, spec.get("schema", {})):
            errors.append({"code": "invalid_query_parameter", "parameter": name})
    for name, spec in query_specs.items():
        if spec.get("required") and name not in query_params:
            errors.append({"code": "missing_query_parameter", "parameter": name})
    if "per_page" in query_specs:
        normalized_query.setdefault("page", 1)
        normalized_query.setdefault("per_page", 20)
        for name in ("page", "per_page"):
            if type(normalized_query[name]) is not int or normalized_query[name] < 1:
                errors.append({"code": "invalid_pagination", "parameter": name})
        if type(normalized_query["per_page"]) is int and normalized_query["per_page"] > 200:
            errors.append({"code": "per_page_exceeds_maximum", "maximum": 200})
    path = op["path"]
    if not errors:
        path = PLACEHOLDER.sub(lambda match: quote(path_params[match.group(1)], safe=""), path)
    return {
        "valid": not errors,
        "executable": not errors,
        "request": {
            "operation_id": operation_id,
            "method": "GET",
            "path": path,
            "query_params": normalized_query,
        } if not errors else None,
        "errors": errors,
        "warnings": [],
        "source": catalog.source,
    }

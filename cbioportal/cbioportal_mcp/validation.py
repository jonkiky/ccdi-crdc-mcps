"""Validate requests against the pinned OpenAPI contract without domain PVs."""

from __future__ import annotations

import json
import re
from urllib.parse import quote, urlencode

from jsonschema import Draft202012Validator

from .catalog import Catalog


PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
BAD_ESCAPE = re.compile(r"%(?:2e|2f|5c|3f|23)", re.IGNORECASE)
MAX_BODY_BYTES = 32_768
MAX_QUERY_BYTES = 8_192


def _safe_segment(value: object) -> bool:
    return (
        isinstance(value, str) and 0 < len(value) <= 256
        and value not in (".", "..") and not BAD_ESCAPE.search(value)
        and not any(c in value for c in "/\\?#")
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def _safe_query_value(value: object) -> bool:
    return (
        type(value) in (str, int, float, bool)
        and (not isinstance(value, str) or (
            len(value) <= 2048 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
        ))
    )


def _matches_schema(value: object, schema: dict, catalog: Catalog) -> bool:
    document = {"allOf": [schema], "components": catalog.spec.get("components", {})}
    return Draft202012Validator(document).is_valid(value)


def validate_request(
    catalog: Catalog, operation_id: str,
    path_params: dict | None = None, query_params: dict | None = None,
    body: object = None,
) -> dict:
    errors: list[dict] = []
    op = catalog.operations.get(operation_id) if isinstance(operation_id, str) else None
    if op is None:
        errors.append({"code": "operation_not_found", "operation_id": operation_id})
    if not isinstance(path_params, (dict, type(None))) or not isinstance(query_params, (dict, type(None))):
        errors.append({"code": "invalid_parameter_object"})
    if errors:
        return {"valid": False, "request": None, "errors": errors, "source": catalog.source}
    path_params = path_params or {}
    query_params = query_params or {}
    path_specs = {p["name"]: p for p in op["parameters"] if p["in"] == "path"}
    query_specs = {p["name"]: p for p in op["parameters"] if p["in"] == "query"}
    for name in sorted(path_specs.keys() - path_params.keys()):
        errors.append({"code": "missing_path_parameter", "parameter": name})
    for name in path_params:
        if not isinstance(name, str) or name not in path_specs:
            errors.append({"code": "unknown_path_parameter", "parameter": str(name)})
        elif not _safe_segment(path_params[name]):
            errors.append({"code": "invalid_path_parameter", "parameter": name})
    if len(query_params) > 32:
        errors.append({"code": "too_many_query_parameters"})
    for name in query_params:
        if not isinstance(name, str) or name not in query_specs:
            errors.append({"code": "unknown_query_parameter", "parameter": str(name)})
        elif not _safe_query_value(query_params[name]) or not _matches_schema(query_params[name], query_specs[name].get("schema", {}), catalog):
            errors.append({"code": "invalid_query_parameter", "parameter": name})
    for name, spec in query_specs.items():
        if spec.get("required") and name not in query_params:
            errors.append({"code": "missing_query_parameter", "parameter": name})
    normalized_query = dict(query_params)
    if "pageSize" in query_specs:
        normalized_query.setdefault("pageSize", 20)
        normalized_query.setdefault("pageNumber", 0)
        if type(normalized_query["pageSize"]) is not int or not 1 <= normalized_query["pageSize"] <= 200:
            errors.append({"code": "invalid_page_size", "maximum": 200})
        if type(normalized_query["pageNumber"]) is not int or normalized_query["pageNumber"] < 0:
            errors.append({"code": "invalid_page_number"})
    if not errors and len(urlencode(normalized_query).encode()) > MAX_QUERY_BYTES:
        errors.append({"code": "query_too_large"})
    body_spec = op["spec"].get("requestBody")
    if op["method"] == "GET" and body is not None:
        errors.append({"code": "body_not_allowed"})
    elif op["method"] == "POST":
        if body is None and body_spec and body_spec.get("required"):
            errors.append({"code": "missing_body"})
        elif body is not None:
            schema = (body_spec or {}).get("content", {}).get("application/json", {}).get("schema")
            if schema is None or not _matches_schema(body, schema, catalog):
                errors.append({"code": "invalid_body"})
            try:
                size = len(json.dumps(body, ensure_ascii=False, allow_nan=False).encode())
            except (TypeError, ValueError, OverflowError, RecursionError):
                errors.append({"code": "invalid_body_json"})
            else:
                if size > MAX_BODY_BYTES:
                    errors.append({"code": "body_too_large", "maximum_bytes": MAX_BODY_BYTES})
    if errors:
        return {"valid": False, "request": None, "errors": errors, "source": catalog.source}
    path = PLACEHOLDER.sub(lambda match: quote(path_params[match.group(1)], safe=""), op["path"])
    return {
        "valid": True,
        "request": {
            "operation_id": operation_id, "method": op["method"],
            "path": path, "query_params": normalized_query, "body": body,
        },
        "errors": [], "source": catalog.source,
    }

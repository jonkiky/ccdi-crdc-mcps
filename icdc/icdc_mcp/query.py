"""Validate bounded, read-only ICDC GraphQL queries and fetch one response."""

from __future__ import annotations

import json
import math

import httpx
from graphql import OperationType, get_named_type, get_variable_values, parse, validate, value_from_ast_untyped
from graphql.language import FieldNode, OperationDefinitionNode

from .catalog import Catalog, GRAPHQL_URL


MAX_QUERY_CHARS = 8_000
MAX_VARIABLE_BYTES = 16_000
MAX_STRING_CHARS = 512
MAX_LIST_ITEMS = 100
MAX_DEPTH = 6
MAX_FIELDS = 80
MAX_DATA_ROOT_FIELDS = 3
MAX_FIRST = 100
MAX_OFFSET = 10_000
MAX_RESPONSE_BYTES = 2_000_000

# This is an allowlist of public metadata roots, not an inferred set of all
# fields in the introspection document. New roots need explicit review.
SCALAR_ROOTS = frozenset({
    "schemaVersion", "numberOfStudies", "numberOfCases", "numberOfSamples",
    "numberOfFiles", "numberOfStudyFiles", "numberOfPrograms",
    "numberOfAliquots", "volumeOfData",
})
DATA_ROOTS = frozenset({
    "studiesByProgram",
    "caseOverview", "studyDetail", "globalSearch",
})
ALLOWED_ROOTS = SCALAR_ROOTS | DATA_ROOTS


class UpstreamFailure(Exception):
    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status


def _bounded_input(value: object) -> bool:
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 8:
            return False
        if isinstance(item, str):
            if len(item) > MAX_STRING_CHARS:
                return False
        elif isinstance(item, list):
            if len(item) > MAX_LIST_ITEMS:
                return False
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, dict):
            if len(item) > MAX_LIST_ITEMS or any(
                not isinstance(key, str) or len(key) > 100 for key in item
            ):
                return False
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, float):
            if not math.isfinite(item):
                return False
        elif item is not None and type(item) not in (bool, int):
            return False
    return True


def _count_fields(selection_set) -> tuple[int, int, list[str]]:
    count = 0
    max_depth = 0
    errors: list[str] = []
    pending = [(selection_set, 1)]
    while pending:
        current, depth = pending.pop()
        max_depth = max(max_depth, depth)
        if depth > MAX_DEPTH:
            continue
        for selection in current.selections:
            if not isinstance(selection, FieldNode):
                errors.append("fragments are not supported")
                continue
            count += 1
            name = selection.name.value
            if name.startswith("__"):
                errors.append("GraphQL introspection fields are not exposed")
            if "manifest" in name.lower() or "download" in name.lower():
                errors.append("manifest and download fields are not exposed")
            if selection.selection_set:
                pending.append((selection.selection_set, depth + 1))
    return count, max_depth, errors


def _nonempty_study_code(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and bool(item.strip()) for item in value
    )


def _check_field_arguments(selection_set, parent_type, variables: dict) -> list[str]:
    """Apply argument and pagination limits to every selected schema field."""
    errors: list[str] = []
    pending = [(selection_set, parent_type, "")]
    while pending:
        current, current_type, prefix = pending.pop()
        for field in current.selections:
            if not isinstance(field, FieldNode):
                continue  # Fragment rejection is handled by _count_fields.
            name = field.name.value
            path = f"{prefix}.{name}" if prefix else name
            definition = getattr(current_type, "fields", {}).get(name)
            if definition is None:
                continue  # GraphQL schema validation reports unknown fields.
            args = {
                arg.name.value: value_from_ast_untyped(arg.value, variables)
                for arg in field.arguments or ()
            }
            if not _bounded_input(args):
                errors.append(f"arguments for {path!r} exceed input limits")
            if "first" in definition.args:
                first = args.get("first")
                if type(first) is not int or not 1 <= first <= MAX_FIRST:
                    errors.append(f"{path}.first must be an explicit integer from 1 to {MAX_FIRST}")
            if "offset" in definition.args or "offset" in args:
                offset = args.get("offset", 0)
                if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
                    errors.append(f"{path}.offset must be an integer from 0 to {MAX_OFFSET}")
            if not prefix and name == "studyDetail" and not _nonempty_study_code(args.get("study_code")):
                errors.append("studyDetail.study_code must be nonempty")
            if not prefix and name == "globalSearch":
                search = args.get("input")
                if not isinstance(search, str) or not 1 <= len(search.strip()) <= 100:
                    errors.append("globalSearch.input must contain 1..100 characters")
            if field.selection_set:
                pending.append((field.selection_set, get_named_type(definition.type), path))
    return errors


def validate_graphql_query(catalog: Catalog, query: str, variables: dict | None = None) -> dict:
    """Validate one operation against the pinned schema and MCP limits offline."""
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        return {"valid": False, "errors": [f"query must contain 1..{MAX_QUERY_CHARS} characters"]}
    if variables is None:
        variables = {}
    if not isinstance(variables, dict) or not _bounded_input(variables):
        return {"valid": False, "errors": ["variables must be a bounded JSON object"]}
    try:
        variable_bytes = len(json.dumps(variables, ensure_ascii=False, allow_nan=False).encode())
    except (TypeError, ValueError, OverflowError, RecursionError):
        return {"valid": False, "errors": ["variables must be a bounded JSON object"]}
    if variable_bytes > MAX_VARIABLE_BYTES:
        return {"valid": False, "errors": ["variables exceed the byte limit"]}
    try:
        document = parse(query)
    except Exception as exc:
        return {"valid": False, "errors": [f"GraphQL syntax error: {exc}"]}
    operations = [node for node in document.definitions if isinstance(node, OperationDefinitionNode)]
    if len(operations) != 1 or len(document.definitions) != 1:
        return {"valid": False, "errors": ["provide exactly one query operation without fragments"]}
    operation = operations[0]
    errors: list[str] = []
    if operation.operation is not OperationType.QUERY:
        errors.append("only GraphQL query operations are allowed")
    field_count, depth, structure_errors = _count_fields(operation.selection_set)
    errors.extend(structure_errors)
    roots = [node for node in operation.selection_set.selections if isinstance(node, FieldNode)]
    scalar_count = sum(root.name.value in SCALAR_ROOTS for root in roots)
    data_count = len(roots) - scalar_count
    if scalar_count > len(SCALAR_ROOTS):
        errors.append(f"at most {len(SCALAR_ROOTS)} scalar root fields are allowed")
    if data_count > MAX_DATA_ROOT_FIELDS:
        errors.append(f"at most {MAX_DATA_ROOT_FIELDS} data root fields are allowed")
    if field_count > MAX_FIELDS or depth > MAX_DEPTH:
        errors.append(f"query exceeds {MAX_FIELDS} fields or depth {MAX_DEPTH}")
    if errors:
        return {"valid": False, "errors": errors, "field_count": field_count, "depth": depth}

    errors.extend(str(error) for error in validate(catalog.schema, document)[:10])
    coerced = get_variable_values(catalog.schema, operation.variable_definitions or (), variables)
    if isinstance(coerced, list):
        errors.extend(str(error) for error in coerced[:10])
        coerced = variables
    elif hasattr(coerced, "coerced"):
        coerced = coerced.coerced

    for root in roots:
        name = root.name.value
        if name not in ALLOWED_ROOTS:
            errors.append(f"root field {name!r} is not enabled for bounded MCP queries")
    errors.extend(_check_field_arguments(operation.selection_set, catalog.schema.query_type, coerced))
    return {
        "valid": not errors,
        "errors": errors,
        "operation_name": operation.name.value if operation.name else None,
        "field_count": field_count,
        "depth": depth,
        "source": catalog.source(),
    }


async def fetch_graphql(
    query: str, variables: dict | None = None, operation_name: str | None = None, *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """POST once to ICDC's fixed endpoint and return its complete bounded JSON."""
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=20.0, follow_redirects=False,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            trust_env=False,
        ) as client:
            async with client.stream(
                "POST", GRAPHQL_URL,
                json={"query": query, "variables": variables or {}, "operationName": operation_name},
            ) as response:
                status = response.status_code
                if 300 <= status < 400:
                    raise UpstreamFailure("redirect_rejected", status)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise UpstreamFailure("response_too_large", status)
    except UpstreamFailure:
        raise
    except httpx.TimeoutException as exc:
        raise UpstreamFailure("upstream_timeout") from exc
    except httpx.HTTPError as exc:
        raise UpstreamFailure("upstream_transport_error") from exc
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise UpstreamFailure("invalid_upstream_json", status) from exc
    if not isinstance(payload, dict):
        raise UpstreamFailure("invalid_upstream_json", status)
    return {"http_status": status, "endpoint": GRAPHQL_URL, "payload": payload}

"""Validate bounded Population Science GraphQL and send one upstream request."""

from __future__ import annotations

import json

import httpx
from graphql import (
    OperationType, get_named_type, get_variable_values, is_list_type,
    is_non_null_type, parse, validate, value_from_ast_untyped,
)
from graphql.language import FieldNode, OperationDefinitionNode

from .catalog import Catalog, GRAPHQL_URL


MAX_QUERY_CHARS = 8_000
MAX_VARIABLE_BYTES = 16_000
MAX_STRING_CHARS = 512
MAX_LIST_ITEMS = 100
MAX_DEPTH = 6
MAX_FIELDS = 80
MAX_ROOT_FIELDS = 3
MAX_FIRST = 100
MAX_OFFSET = 10_000
MAX_RESPONSE_BYTES = 2_000_000
ALLOWED_ROOTS = frozenset({
    "searchStudies", "tabStudy", "studyDemographics", "primarySiteMorphology",
    "studyFiles", "studyGeneral", "dataCollectionPage", "globalStatsBar",
    "minMaxBoundQuery",
})
PAGED_ROOTS = frozenset({"tabStudy", "studyDemographics", "primarySiteMorphology", "studyFiles"})
STUDY_SCOPED_ROOTS = frozenset({"studyGeneral", "dataCollectionPage", "globalStatsBar", "studyDemographics"})
SUMMARY_LIST_TYPES = frozenset({"TypeCount", "GroupCounts", "DiagnosisCodes", "data_collection"})
BLOCKED_FIELDS = frozenset({"data_file_signed_url", "data_file_location", "drs_uri", "file_location"})
BLOCKED_ARGUMENTS = frozenset({"filter", "order_by", "sort_direction", "data_file_signed_url", "data_file_location"})


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
        elif item is not None and not isinstance(item, (bool, int, float)):
            return False
    return True


def _count_fields(selection_set) -> tuple[int, int, list[str]]:
    count = 0
    max_depth = 0
    errors = []
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
            if selection.name.value.startswith("__") and selection.name.value != "__typename":
                errors.append("GraphQL introspection fields are not exposed through run_graphql_query")
            if selection.selection_set:
                pending.append((selection.selection_set, depth + 1))
    return count, max_depth, errors


def _is_object_list(graphql_type: object) -> bool:
    current = graphql_type
    while is_non_null_type(current):
        current = current.of_type
    return is_list_type(current) and hasattr(get_named_type(current), "fields")


def _selection_errors(catalog: Catalog, roots: list[FieldNode]) -> list[str]:
    """Allow only bounded root lists and small study summary sublists."""
    errors = []

    def visit(selection_set, parent_type, path: str) -> None:
        if not selection_set:
            return
        parent = get_named_type(parent_type)
        if not hasattr(parent, "fields"):
            return
        for selected in selection_set.selections:
            if not isinstance(selected, FieldNode) or selected.name.value == "__typename":
                continue
            name = selected.name.value
            definition = parent.fields.get(name)
            if definition is None:
                continue  # GraphQL schema validation reports it separately.
            child_path = f"{path}.{name}"
            if name in BLOCKED_FIELDS:
                errors.append(f"field {child_path} is not exposed")
            if _is_object_list(definition.type) and get_named_type(definition.type).name not in SUMMARY_LIST_TYPES:
                errors.append(f"nested collection {child_path} is not enabled")
            visit(selected.selection_set, definition.type, child_path)

    for root in roots:
        definition = catalog.schema.query_type.fields.get(root.name.value)
        if definition is not None:
            visit(root.selection_set, definition.type, root.name.value)
    return errors


def _pv_checks(catalog: Catalog, roots: list[FieldNode], variables: dict) -> list[dict]:
    checks = []
    for root in roots:
        for argument in root.arguments or ():
            name = argument.name.value
            prop = catalog.properties.get(name)
            if not prop or not isinstance(prop.get("Enum"), list):
                continue
            value = value_from_ast_untyped(argument.value, variables)
            if value is None:
                continue
            values = value if isinstance(value, list) else [value]
            unlisted = [item for item in values if item not in prop["Enum"]]
            checks.append({
                "root": root.name.value, "argument": name, "model_property": name,
                "all_listed": not unlisted, "unlisted_values": unlisted[:20],
                "strict": prop.get("Strict"), "advisory_only": True,
            })
    return checks


def validate_graphql_query(catalog: Catalog, query: str, variables: dict | None = None) -> dict:
    errors: list[str] = []
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        return {"valid": False, "errors": [f"query must contain 1..{MAX_QUERY_CHARS} characters"]}
    if variables is None:
        variables = {}
    if not isinstance(variables, dict) or not _bounded_input(variables):
        return {"valid": False, "errors": ["variables must be a bounded JSON object"]}
    if len(json.dumps(variables, ensure_ascii=False).encode()) > MAX_VARIABLE_BYTES:
        return {"valid": False, "errors": ["variables exceed the byte limit"]}
    try:
        document = parse(query)
    except Exception as exc:
        return {"valid": False, "errors": [f"GraphQL syntax error: {exc}"]}
    operations = [node for node in document.definitions if isinstance(node, OperationDefinitionNode)]
    if len(operations) != 1 or len(document.definitions) != 1:
        errors.append("provide exactly one query operation without fragments")
    if errors:
        return {"valid": False, "errors": errors}
    operation = operations[0]
    if operation.operation is not OperationType.QUERY:
        errors.append("only GraphQL query operations are allowed")
    field_count, depth, structure_errors = _count_fields(operation.selection_set)
    errors.extend(structure_errors)
    roots = [node for node in operation.selection_set.selections if isinstance(node, FieldNode)]
    if len(roots) > MAX_ROOT_FIELDS:
        errors.append(f"at most {MAX_ROOT_FIELDS} root fields are allowed")
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
    errors.extend(_selection_errors(catalog, roots))
    for root in roots:
        name = root.name.value
        if name not in ALLOWED_ROOTS:
            errors.append(f"root field {name!r} is not enabled for bounded MCP queries")
            continue
        args = {arg.name.value: value_from_ast_untyped(arg.value, coerced) for arg in root.arguments or ()}
        if not _bounded_input(args):
            errors.append(f"arguments for {name!r} exceed input limits")
        for key in args:
            if key in BLOCKED_ARGUMENTS or key.endswith("_matches"):
                errors.append(f"argument {name}.{key} is not enabled")
        if name in PAGED_ROOTS:
            first = args.get("first")
            if type(first) is not int or not 1 <= first <= MAX_FIRST:
                errors.append(f"{name}.first must be an explicit integer from 1 to {MAX_FIRST}")
            offset = args.get("offset", 0)
            if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
                errors.append(f"{name}.offset must be an integer from 0 to {MAX_OFFSET}")
        if name in STUDY_SCOPED_ROOTS:
            studies = args.get("study_short_name")
            if not isinstance(studies, list) or not 1 <= len(studies) <= 20 or any(
                not isinstance(item, str) or not item.strip() for item in studies
            ):
                errors.append(f"{name}.study_short_name requires 1..20 nonempty study names")
    return {
        "valid": not errors,
        "errors": errors,
        "field_count": field_count,
        "depth": depth,
        "pv_checks": _pv_checks(catalog, roots, coerced) if not errors else [],
        "pv_note": "PV checks compare exact GraphQL argument names with PopSci model Enum properties; the model does not define GraphQL filter validity or matching semantics.",
        "source": catalog.source(),
    }


async def fetch_graphql(
    query: str, variables: dict | None = None, *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Make exactly one POST to the fixed PopSci endpoint and preserve JSON."""
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=20.0, follow_redirects=False,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        ) as client:
            async with client.stream(
                "POST", GRAPHQL_URL, json={"query": query, "variables": variables or {}},
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

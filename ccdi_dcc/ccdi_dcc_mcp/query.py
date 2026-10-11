"""Validate bounded CCDI DCC GraphQL and send one upstream request."""

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
# Only entity collections, their cursor connections, and aggregates are live.
# Generated relationship-edge and internal memgraph roots remain discoverable
# offline but are not enabled for execution.
ENTITY_ROOTS = frozenset({
    "cellLines", "clinicalMeasureFiles", "consentGroups", "cytogenomicFiles",
    "diagnoses", "exposures", "familyRelationships", "genericFiles",
    "geneticAnalyses", "laboratoryTests", "medicalHistories",
    "methylationArrayFiles", "participants", "pathologyFiles", "pdxes",
    "publications", "radiologyFiles", "samples", "sequencingFiles",
    "studies", "studyAdmins", "studyArms", "studyFundings",
    "studyPersonnels", "studyStatuses", "survivals", "synonyms",
    "treatmentChemotherapies", "treatmentOthers", "treatmentRadiations",
    "treatmentResponses", "treatmentSurgeries",
})
ALLOWED_ROOTS = frozenset(
    name + suffix for name in ENTITY_ROOTS for suffix in ("", "Connection", "Aggregate")
)


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


def _pv_checks(catalog: Catalog, root_fields: list[FieldNode], variables: dict) -> list[dict]:
    """Check only exact-match where operators against exact model property names."""
    checks = []

    def visit(where: object, root: str) -> None:
        if isinstance(where, list):
            for item in where:
                visit(item, root)
        elif isinstance(where, dict):
            for key, value in where.items():
                if key in {"AND", "OR", "NOT"}:
                    visit(value, root)
                    continue
                property_name = key.removesuffix("_EQ").removesuffix("_IN").removesuffix("_INCLUDES")
                if key != property_name and not key.endswith(("_EQ", "_IN", "_INCLUDES")):
                    continue
                prop = catalog.properties.get(property_name)
                if not prop or not isinstance(prop.get("Enum"), list) or value is None:
                    continue
                values = value if isinstance(value, list) else [value]
                unlisted = [item for item in values if item not in prop["Enum"]]
                checks.append({
                    "root": root, "where_field": key, "model_property": property_name,
                    "all_listed": not unlisted, "unlisted_values": unlisted[:20],
                    "strict": prop.get("Strict"), "advisory_only": True,
                })

    for root in root_fields:
        for argument in root.arguments or ():
            if argument.name.value == "where":
                visit(value_from_ast_untyped(argument.value, variables), root.name.value)
    return checks


def _is_object_list(graphql_type: object) -> bool:
    current = graphql_type
    while is_non_null_type(current):
        current = current.of_type
    return is_list_type(current) and hasattr(get_named_type(current), "fields")


def _nested_list_errors(catalog: Catalog, roots: list[FieldNode]) -> list[str]:
    """Prevent unbounded relationship expansion below the selected root page."""
    errors = []

    def visit(selection_set, parent_type, path: str) -> None:
        if not selection_set:
            return
        parent = get_named_type(parent_type)
        if not hasattr(parent, "fields"):
            return  # GraphQL schema validation reports selections on scalar types.
        for selected in selection_set.selections:
            if not isinstance(selected, FieldNode) or selected.name.value == "__typename":
                continue
            definition = parent.fields.get(selected.name.value)
            if definition is None:
                continue  # GraphQL schema validation reports this separately.
            child_path = f"{path}.{selected.name.value}"
            if get_named_type(definition.type).name.endswith("Connection"):
                errors.append(f"nested connection {child_path} is not enabled")
            if _is_object_list(definition.type) and not (
                parent.name.endswith("Connection") and selected.name.value == "edges"
            ):
                errors.append(f"nested collection {child_path} is not enabled")
            visit(selected.selection_set, definition.type, child_path)

    for root in roots:
        definition = catalog.schema.query_type.fields.get(root.name.value)
        if definition is not None:
            visit(root.selection_set, definition.type, root.name.value)
    return errors


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
    schema_errors = validate(catalog.schema, document)
    errors.extend(str(error) for error in schema_errors[:10])
    coerced = get_variable_values(catalog.schema, operation.variable_definitions or (), variables)
    if isinstance(coerced, list):
        errors.extend(str(error) for error in coerced[:10])
        coerced = variables
    elif hasattr(coerced, "coerced"):
        coerced = coerced.coerced
    errors.extend(_nested_list_errors(catalog, roots))
    for root in roots:
        name = root.name.value
        if name not in ALLOWED_ROOTS:
            errors.append(f"root field {name!r} is not enabled for bounded MCP queries")
            continue
        args = {arg.name.value: value_from_ast_untyped(arg.value, coerced) for arg in root.arguments or ()}
        if not _bounded_input(args):
            errors.append(f"arguments for {name!r} exceed input limits")
        if "options" in args:
            errors.append(f"{name}.options is not enabled; use explicit limit and offset")
        if name.endswith("Connection"):
            first = args.get("first")
            if type(first) is not int or not 1 <= first <= MAX_FIRST:
                errors.append(f"{name}.first must be an explicit integer from 1 to {MAX_FIRST}")
        elif name in ENTITY_ROOTS:
            limit = args.get("limit")
            if type(limit) is not int or not 1 <= limit <= MAX_FIRST:
                errors.append(f"{name}.limit must be an explicit integer from 1 to {MAX_FIRST}")
            offset = args.get("offset", 0)
            if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
                errors.append(f"{name}.offset must be an integer from 0 to {MAX_OFFSET}")
    return {
        "valid": not errors,
        "errors": errors,
        "field_count": field_count,
        "depth": depth,
        "pv_checks": _pv_checks(catalog, roots, coerced) if not errors else [],
        "pv_note": "PV checks compare exact where EQ, IN, and INCLUDES values with same-name CCDI model Enum properties; GraphQL schema validation controls filter validity, and model PV membership is advisory.",
        "source": catalog.source(),
    }


async def fetch_graphql(
    query: str, variables: dict | None = None, *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Make exactly one POST to the fixed CCDI DCC endpoint and preserve JSON."""
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

"""Load operation and exact PV reference data from the checked-in skill."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml


ENTITIES = ("subject", "sample", "file")
APPROVED_ORIGIN = "https://federation.ccdi.cancer.gov/api/v1"
DEFAULT_REFERENCE_DIR = (
    Path(__file__).resolve().parents[2]
    / "skills/ccdi-federation-ai-copilot/references"
)


class Catalog:
    def __init__(self, reference_dir: Path | None = None):
        self.reference_dir = Path(
            reference_dir or os.environ.get("CCDI_REFERENCE_DIR", DEFAULT_REFERENCE_DIR)
        )
        openapi_bytes = (self.reference_dir / "openapi.yml").read_bytes()
        self.openapi = yaml.safe_load(openapi_bytes)
        if not isinstance(self.openapi, dict) or not isinstance(self.openapi.get("paths"), dict) or not self.openapi["paths"]:
            raise ValueError("invalid OpenAPI asset")
        self.version = self.openapi["info"]["version"]
        self.openapi_sha256 = hashlib.sha256(openapi_bytes).hexdigest()
        self.origin = self.openapi["servers"][0]["url"].rstrip("/")
        if self.origin != APPROVED_ORIGIN:
            raise ValueError("OpenAPI origin differs from the approved CCDI API")

        self.operations: dict[str, dict] = {}
        for path, methods in self.openapi["paths"].items():
            common_params = methods.get("parameters", [])
            for method, spec in methods.items():
                if method == "parameters":
                    continue
                if method.lower() != "get":
                    raise ValueError(f"non-GET operation: {method} {path}")
                operation_id = spec["operationId"]
                if operation_id in self.operations:
                    raise ValueError(f"duplicate operation ID: {operation_id}")
                params = common_params + spec.get("parameters", [])
                if any("$ref" in p for p in params):
                    raise ValueError(f"unresolved parameter reference: {operation_id}")
                self.operations[operation_id] = {
                    "method": "GET", "path": path, "spec": spec, "parameters": params,
                }

        self.pv: dict[str, dict] = {}
        self.pv_hashes: dict[str, str] = {}
        for entity in ENTITIES:
            raw = (self.reference_dir / "pv" / f"{entity}-pv-metadata.json").read_bytes()
            data = json.loads(raw)
            if not isinstance(data, dict) or any(
                not isinstance(v, dict)
                or "permissible_values" not in v
                or (v["permissible_values"] is not None and (
                    not isinstance(v["permissible_values"], list)
                    or any(not isinstance(pv, dict) or not isinstance(pv.get("value"), str)
                           for pv in v["permissible_values"])
                ))
                for v in data.values()
            ):
                raise ValueError(f"invalid {entity} PV asset")
            self.pv[entity] = data
            self.pv_hashes[entity] = hashlib.sha256(raw).hexdigest()

    @property
    def source(self) -> dict:
        return {
            "openapi_version": self.version,
            "openapi_sha256": self.openapi_sha256,
            "pv_sha256": self.pv_hashes,
        }

    @staticmethod
    def classify(path: str) -> tuple[str, str]:
        if path.startswith("/metadata/fields/"):
            return path.rsplit("/", 1)[-1], "metadata"
        entity = path.split("/")[1].split("-")[0]
        if entity not in ENTITIES:
            entity = "other"
        if path.endswith("/count"):
            kind = "count"
        elif path.endswith("/summary"):
            kind = "summary"
        elif "{" in path:
            kind = "detail"
        else:
            kind = "collection"
        return entity, kind

    def list_operations(self, entity: str | None = None, kind: str | None = None) -> dict:
        if entity is not None and entity not in (*ENTITIES, "other"):
            return {"error": "invalid_entity", "entity": entity}
        if kind is not None and kind not in ("collection", "detail", "count", "summary", "metadata"):
            return {"error": "invalid_kind", "kind": kind}
        items = []
        for operation_id, op in self.operations.items():
            op_entity, op_kind = self.classify(op["path"])
            if entity is not None and entity != op_entity:
                continue
            if kind is not None and kind != op_kind:
                continue
            items.append({
                "operation_id": operation_id,
                "method": "GET",
                "path": op["path"],
                "summary": op["spec"].get("summary", ""),
                "entity": op_entity,
                "kind": op_kind,
                "paginated": any(p["name"] == "per_page" for p in op["parameters"]),
            })
        return {"operations": items, "total": len(items), "source": self.source}

    def describe_operation(self, operation_id: str) -> dict:
        op = self.operations.get(operation_id)
        if op is None:
            return {"error": "operation_not_found", "operation_id": operation_id}
        paginated = any(p["name"] == "per_page" for p in op["parameters"])
        return {
            "operation_id": operation_id,
            "method": "GET",
            "path": op["path"],
            "summary": op["spec"].get("summary", ""),
            "description": op["spec"].get("description", ""),
            "parameters": op["parameters"],
            "responses": op["spec"].get("responses", {}),
            "pagination": {"default_page": 1, "default_per_page": 20, "max_per_page": 200}
                if paginated else None,
            "source": self.source,
        }

    def get_field_metadata(self, entity: str, field: str) -> dict:
        if entity not in self.pv:
            return {"error": "unsupported_entity", "entity": entity}
        if field not in self.pv[entity]:
            return {"error": "field_not_found", "entity": entity, "field": field}
        data = self.pv[entity][field]
        values = data.get("permissible_values")
        return {
            "entity": entity,
            "field": field,
            "formal_name": data.get("formal_name"),
            "description": data.get("cde_description"),
            "cadsr_link": data.get("cadsr_link"),
            "controlled": values is not None,
            "pv_count": len(values) if values is not None else None,
            "source": self.source,
        }

    def list_permissible_values(
        self, entity: str, field: str | None = None, offset: int = 0, limit: int = 100
    ) -> dict:
        if entity not in self.pv:
            return {"error": "unsupported_entity", "entity": entity}
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            return {"error": "invalid_pagination", "offset": offset, "limit": limit}
        if field is not None and field not in self.pv[entity]:
            return {"error": "field_not_found", "entity": entity, "field": field}
        if field is not None and self.pv[entity][field].get("permissible_values") is None:
            return {"error": "no_bundled_pvs", "entity": entity, "field": field}
        fields = [field] if field else sorted(self.pv[entity])
        entries = [
            {"field": name, **value}
            for name in fields
            for value in (self.pv[entity][name].get("permissible_values") or [])
        ]
        end = offset + limit
        return {
            "entity": entity, "field": field, "values": entries[offset:end],
            "offset": offset, "limit": limit, "total": len(entries),
            "next_offset": end if end < len(entries) else None,
            "source": self.source,
        }

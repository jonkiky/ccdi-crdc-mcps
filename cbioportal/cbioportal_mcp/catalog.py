"""Pinned OpenAPI contract for the CCDI cBioPortal public API."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


API_ORIGIN = "https://cbioportal-api.ccdi.cancer.gov"
SPEC_URL = API_ORIGIN + "/api/v3/api-docs/public"
DEFAULT_SPEC = Path(__file__).parent / "assets" / "openapi.json"


class Catalog:
    def __init__(self, spec_path: Path = DEFAULT_SPEC):
        raw = Path(spec_path).read_bytes()
        spec = json.loads(raw)
        if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
            raise ValueError("invalid OpenAPI document")
        if spec.get("servers") != [{"url": API_ORIGIN}]:
            raise ValueError("OpenAPI origin differs from approved CCDI cBioPortal origin")
        self.spec = spec
        self.source = {
            "openapi_url": SPEC_URL,
            "openapi_version": spec["openapi"],
            "openapi_sha256": hashlib.sha256(raw).hexdigest(),
        }
        self.operations: dict[str, dict] = {}
        for path, methods in spec["paths"].items():
            if not path.startswith("/api/"):
                raise ValueError(f"unexpected API path: {path}")
            for method, operation in methods.items():
                if method not in ("get", "post") or (method == "post" and not path.endswith("/fetch")):
                    raise ValueError(f"unexpected method or path: {method} {path}")
                operation_id = operation.get("operationId")
                if not isinstance(operation_id, str) or operation_id in self.operations:
                    raise ValueError(f"missing or duplicate operation ID: {operation_id}")
                parameters = operation.get("parameters", [])
                if any("$ref" in parameter for parameter in parameters):
                    raise ValueError(f"unresolved parameter reference: {operation_id}")
                self.operations[operation_id] = {
                    "method": method.upper(), "path": path, "spec": operation,
                    "parameters": parameters,
                }

    def list_operations(
        self, tag: str | None = None, method: str | None = None,
        offset: int = 0, limit: int = 100,
    ) -> dict:
        if method is not None and method not in ("GET", "POST"):
            return {"error": "invalid_method", "method": method}
        if tag is not None and tag not in self.tags:
            return {"error": "invalid_tag", "tag": tag, "available_tags": self.tags}
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            return {"error": "invalid_pagination"}
        items = []
        for operation_id, op in self.operations.items():
            tags = op["spec"].get("tags", [])
            if (tag is not None and tag not in tags) or (method is not None and method != op["method"]):
                continue
            items.append({
                "operation_id": operation_id,
                "method": op["method"], "path": op["path"],
                "tags": tags, "summary": op["spec"].get("summary") or op["spec"].get("description", ""),
                "paginated": any(p["name"] == "pageSize" and p["in"] == "query" for p in op["parameters"]),
            })
        return {
            "operations": items[offset:offset + limit], "total": len(items),
            "next_offset": offset + limit if offset + limit < len(items) else None,
            "available_tags": self.tags, "source": self.source,
        }

    @property
    def tags(self) -> list[str]:
        return sorted({tag for op in self.operations.values() for tag in op["spec"].get("tags", [])})

    def describe_operation(self, operation_id: str) -> dict:
        op = self.operations.get(operation_id)
        if op is None:
            return {"error": "operation_not_found", "operation_id": operation_id, "source": self.source}
        spec = op["spec"]
        return {
            "operation_id": operation_id,
            "method": op["method"], "path": op["path"],
            "tags": spec.get("tags", []),
            "summary": spec.get("summary", ""), "description": spec.get("description", ""),
            "parameters": op["parameters"],
            "request_body": spec.get("requestBody"),
            "responses": spec.get("responses", {}),
            "pagination_policy": {"pageSize_default": 20, "pageSize_maximum": 200, "pageNumber_default": 0}
            if any(p["name"] == "pageSize" and p["in"] == "query" for p in op["parameters"]) else None,
            "source": self.source,
        }

    def describe_schema(self, name: str) -> dict:
        schema = self.spec.get("components", {}).get("schemas", {}).get(name)
        if schema is None:
            return {"error": "schema_not_found", "schema": name, "source": self.source}
        return {"name": name, "schema": schema, "source": self.source}

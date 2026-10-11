"""Bundled public GraphQL schema for offline discovery and validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from graphql import build_client_schema, get_named_type


GRAPHQL_URL = "https://clinical.datacommons.cancer.gov/v1/graphql/"
ASSETS = Path(__file__).parent / "assets"


class Catalog:
    def __init__(self, asset_dir: Path = ASSETS):
        raw = (Path(asset_dir) / "graphql-introspection.json").read_bytes()
        document = json.loads(raw)
        if document.get("errors") or not isinstance(document.get("data", {}).get("__schema"), dict):
            raise ValueError("invalid Clinical Data Commons GraphQL introspection asset")
        self.schema = build_client_schema(document["data"])
        self.schema_sha256 = hashlib.sha256(raw).hexdigest()

    def source(self) -> dict:
        return {"graphql_endpoint": GRAPHQL_URL, "graphql_schema_sha256": self.schema_sha256}

    @staticmethod
    def _page(offset: int, limit: int) -> bool:
        return type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 100

    def list_query_fields(self, offset: int = 0, limit: int = 100) -> dict:
        if not self._page(offset, limit):
            return {"error": "invalid_page", "message": "offset must be >= 0 and limit 1..100"}
        names = sorted(self.schema.query_type.fields)
        page = names[offset:offset + limit]
        return {
            "fields": [{"name": name, "return_type": str(self.schema.query_type.fields[name].type)} for name in page],
            "total": len(names),
            "next_offset": offset + len(page) if offset + len(page) < len(names) else None,
            "source": self.source(),
        }

    def describe_query_field(self, field: str) -> dict:
        definition = self.schema.query_type.fields.get(field)
        if definition is None:
            return {"error": "unknown_query_field", "field": field, "source": self.source()}
        return {
            "field": field,
            "description": definition.description,
            "return_type": str(definition.type),
            "named_return_type": get_named_type(definition.type).name,
            "arguments": [
                {"name": name, "type": str(arg.type), "description": arg.description}
                for name, arg in definition.args.items()
            ],
            "source": self.source(),
        }

    def describe_type(self, type_name: str, offset: int = 0, limit: int = 100) -> dict:
        if not self._page(offset, limit):
            return {"error": "invalid_page", "message": "offset must be >= 0 and limit 1..100"}
        if not isinstance(type_name, str) or type_name.startswith("__"):
            return {"error": "unknown_type", "type": type_name, "source": self.source()}
        definition = self.schema.type_map.get(type_name)
        if definition is None:
            return {"error": "unknown_type", "type": type_name, "source": self.source()}
        result = {
            "type": type_name,
            "kind": type(definition).__name__,
            "description": definition.description,
            "source": self.source(),
        }
        if hasattr(definition, "fields"):
            names = list(definition.fields)
            page = names[offset:offset + limit]
            result["fields"] = [
                {"name": name, "type": str(definition.fields[name].type), "description": definition.fields[name].description}
                for name in page
            ]
            result["total"] = len(names)
            result["next_offset"] = offset + len(page) if offset + len(page) < len(names) else None
        elif hasattr(definition, "values"):
            names = list(definition.values)
            page = names[offset:offset + limit]
            result["enum_values"] = page
            result["total"] = len(names)
            result["next_offset"] = offset + len(page) if offset + len(page) < len(names) else None
        return result

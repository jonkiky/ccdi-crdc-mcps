"""Immutable GraphQL schema and model-property reference data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from graphql import build_client_schema, get_named_type


ASSETS = Path(__file__).parent / "assets"
GRAPHQL_URL = "https://clinicalcommons.ccdi.cancer.gov/v1/graphql/"
MODEL_SOURCE = {
    "file": "ccdi-model-props.yml",
    "url": "https://github.com/CBIIT/ccdi-model/blob/23ee44b40ef15bb54d2aec77a695965471dda5f6/model-desc/ccdi-model-props.yml",
}


class Catalog:
    def __init__(self, asset_dir: Path = ASSETS):
        self.asset_dir = Path(asset_dir)
        raw_schema = (self.asset_dir / "graphql-introspection.json").read_bytes()
        parsed = json.loads(raw_schema)
        if parsed.get("errors") or not isinstance(parsed.get("data", {}).get("__schema"), dict):
            raise ValueError("invalid GraphQL introspection asset")
        self.schema = build_client_schema(parsed["data"])
        self.schema_sha256 = hashlib.sha256(raw_schema).hexdigest()
        raw = (self.asset_dir / MODEL_SOURCE["file"]).read_bytes()
        document = yaml.safe_load(raw)
        props = document.get("PropDefinitions") if isinstance(document, dict) else None
        if not isinstance(props, dict) or not props:
            raise ValueError("invalid CCDI model property asset")
        self.properties = props
        self.model_sha256 = hashlib.sha256(raw).hexdigest()

    def source(self) -> dict:
        return {
            "graphql_endpoint": GRAPHQL_URL,
            "graphql_schema_sha256": self.schema_sha256,
            "model": "ccdi",
            "model_url": MODEL_SOURCE["url"],
            "model_sha256": self.model_sha256,
        }

    def list_properties(
        self, enum_only: bool = False,
        prefix: str | None = None, offset: int = 0, limit: int = 100,
    ) -> dict:
        props = self.properties
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            return {"error": "invalid_page", "message": "offset must be >= 0 and limit 1..200"}
        if prefix is not None and not isinstance(prefix, str):
            return {"error": "invalid_prefix"}
        names = [
            name for name in sorted(props)
            if (not enum_only or isinstance(props[name].get("Enum"), list))
            and (prefix is None or name.startswith(prefix))
        ]
        page = names[offset:offset + limit]
        return {
            "properties": [
                {
                    "name": name,
                    "description": props[name].get("Desc"),
                    "has_enum": isinstance(props[name].get("Enum"), list),
                    "enum_count": len(props[name].get("Enum") or []),
                    "strict": props[name].get("Strict"),
                }
                for name in page
            ],
            "total": len(names),
            "next_offset": offset + len(page) if offset + len(page) < len(names) else None,
            "source": self.source(),
        }

    def describe_property(self, property_name: str) -> dict:
        prop = self.properties.get(property_name)
        if prop is None:
            return {"error": "unknown_property", "property": property_name, "source": self.source()}
        return {
            "property": property_name,
            "description": prop.get("Desc"),
            "type": prop.get("Type"),
            "required": prop.get("Req"),
            "strict": prop.get("Strict"),
            "private": prop.get("Private"),
            "terms": prop.get("Term"),
            "has_enum": isinstance(prop.get("Enum"), list),
            "enum_count": len(prop.get("Enum") or []),
            "source": self.source(),
        }

    def list_permissible_values(
        self, property_name: str, offset: int = 0, limit: int = 100,
    ) -> dict:
        info = self.describe_property(property_name)
        if "error" in info:
            return info
        if not info["has_enum"]:
            return {"error": "no_enum", "property": property_name, "source": self.source()}
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            return {"error": "invalid_page", "message": "offset must be >= 0 and limit 1..200"}
        values = self.properties[property_name]["Enum"]
        page = values[offset:offset + limit]
        return {
            "property": property_name,
            "values": page,
            "total": len(values),
            "strict": info["strict"],
            "next_offset": offset + len(page) if offset + len(page) < len(values) else None,
            "source": self.source(),
        }

    def check_permissible_value(self, property_name: str, value: str) -> dict:
        info = self.describe_property(property_name)
        if "error" in info:
            return info
        if not info["has_enum"]:
            return {"error": "no_enum", "property": property_name, "source": self.source()}
        if not isinstance(value, str):
            return {"error": "invalid_value", "message": "value must be a string"}
        return {
            "property": property_name,
            "value": value,
            "listed": value in self.properties[property_name]["Enum"],
            "strict": info["strict"],
            "source": self.source(),
        }

    def list_query_fields(self, offset: int = 0, limit: int = 100) -> dict:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
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

    def describe_type(self, type_name: str) -> dict:
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
            result["fields"] = [
                {"name": name, "type": str(field.type), "description": field.description}
                for name, field in definition.fields.items()
            ]
        if hasattr(definition, "values"):
            result["enum_values"] = list(definition.values)
        return result

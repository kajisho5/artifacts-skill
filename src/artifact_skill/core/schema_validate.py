"""Minimal, dependency-free JSON-Schema-subset validator.

This exists specifically so `OperationSpec.args_schema` (`adapters/base.py`)
and `ToolContract.input_schema` (`core/contract.py`) are actually enforced
at the boundary that reads them, not just descriptive metadata a caller
could ignore — see the "SPEC" discussion in `docs/contract.md` for why a
schema nobody validates against is exactly the kind of drift-prone,
hand-authored duplication this project tries to avoid elsewhere.

It supports exactly the subset of JSON Schema this project's own schemas
use: `type`, `properties`, `required`, `additionalProperties`, `items`,
`minItems`, `enum`, `minimum`, `maximum`, `exclusiveMinimum`. This is a
deliberate, small implementation, not a general one: adding the
`jsonschema` PyPI package would put a new mandatory dependency on `core/`,
which this project has kept at zero (`pyproject.toml`'s
`dependencies = []`) as part of being local-first. If a future schema
needs a JSON Schema feature this module doesn't cover, that is a signal to
keep the schema simple, not a reason to reach for a general validator.
"""

from __future__ import annotations

from typing import Any

_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def validate_against_schema(value: Any, schema: dict[str, Any] | None, *, path: str = "$") -> list[str]:
    """Return a list of human-readable violations. Empty list == valid."""
    errors: list[str] = []
    if schema:
        _validate(value, schema, path, errors)
    return errors


def _validate(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    schema_type = schema.get("type")
    if schema_type is not None and not _check_type(value, schema_type):
        errors.append(f"{path}: expected type '{schema_type}', got '{type(value).__name__}'")
        return  # further checks against a wrong-typed value would be noise

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")

    if schema_type == "object":
        _validate_object(value, schema, path, errors)
    elif schema_type == "array":
        _validate_array(value, schema, path, errors)
    elif schema_type in ("number", "integer"):
        _validate_number(value, schema, path, errors)


def _check_type(value: Any, schema_type: str) -> bool:
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    expected = _TYPE_MAP.get(schema_type)
    if expected is None:
        return True  # a type keyword this module doesn't model: don't fail closed on it
    return isinstance(value, expected)


def _validate_object(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        return
    properties: dict[str, Any] = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in value:
            errors.append(f"{path}: missing required property '{key}'")
    if schema.get("additionalProperties") is False:
        for key in value:
            if key not in properties:
                errors.append(f"{path}: unexpected property '{key}' (additionalProperties: false)")
    for key, sub_schema in properties.items():
        if key in value:
            _validate(value[key], sub_schema, f"{path}.{key}", errors)


def _validate_array(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        return
    min_items = schema.get("minItems")
    if min_items is not None and len(value) < min_items:
        errors.append(f"{path}: expected at least {min_items} item(s), got {len(value)}")
    item_schema = schema.get("items")
    if item_schema:
        for i, item in enumerate(value):
            _validate(item, item_schema, f"{path}[{i}]", errors)


def _validate_number(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{path}: {value} is below minimum {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        errors.append(f"{path}: {value} is above maximum {schema['maximum']}")
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        errors.append(f"{path}: {value} must be strictly greater than {schema['exclusiveMinimum']}")

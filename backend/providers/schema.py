"""Strict JSON-schema builder for OpenAI structured outputs, plus a local validator.

OpenAI strict mode requires, on every object: all properties listed in
`required` and `additionalProperties: false`. Optional fields are therefore
expressed as nullable. The root must be an object (not anyOf). Composition
other than anyOf is not supported. Limits used here follow the OpenAI
structured-outputs documentation as read during the engineering review:
nesting depth 10, 5000 object properties, 1000 enum values.

The local validator re-checks every response against the same schema. Strict
mode should make that redundant, but the spec (section 6) requires a
schema-invalid path with one retry, and a non-strict fallback or a provider
bug must never reach the pipeline unchecked.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .errors import SchemaBuildError

MAX_DEPTH = 10
MAX_PROPERTIES = 5000
MAX_ENUM_VALUES = 1000
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_UNSUPPORTED = ("oneOf", "allOf", "not", "if", "then", "else", "patternProperties",
                "dependentRequired", "dependentSchemas", "unevaluatedProperties", "propertyNames")
_STRIP = ("default",)


# ------------------------------------------------------------------ builder


def strict_json_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return a strict-mode copy of `schema`. The input is not modified."""
    out = copy.deepcopy(dict(schema))
    if out.get("type") != "object" or "anyOf" in out:
        raise SchemaBuildError("root schema must be a plain object (type: object, no anyOf)")
    counts = {"props": 0, "enums": 0}
    defs_key = "$defs" if "$defs" in out else ("definitions" if "definitions" in out else None)
    if defs_key:
        for name, sub in out[defs_key].items():
            out[defs_key][name] = _normalize(sub, depth=1, counts=counts, path=f"{defs_key}.{name}")
    body = {k: v for k, v in out.items() if k != defs_key}
    result = _normalize(body, depth=0, counts=counts, path="$")
    if defs_key:
        result[defs_key] = out[defs_key]
    if counts["props"] > MAX_PROPERTIES:
        raise SchemaBuildError(f"schema has {counts['props']} properties, strict mode allows {MAX_PROPERTIES}")
    if counts["enums"] > MAX_ENUM_VALUES:
        raise SchemaBuildError(f"schema has {counts['enums']} enum values, strict mode allows {MAX_ENUM_VALUES}")
    return result


def _normalize(node: Any, *, depth: int, counts: dict[str, int], path: str) -> Any:
    if not isinstance(node, dict):
        return node
    for key in _UNSUPPORTED:
        if key in node:
            raise SchemaBuildError(f"{path}: '{key}' is not supported in strict mode")
    node = {k: v for k, v in node.items() if k not in _STRIP}
    if "enum" in node:
        counts["enums"] += len(node["enum"])

    types = node.get("type")
    is_object = types == "object" or (isinstance(types, list) and "object" in types) or "properties" in node
    if is_object:
        if depth >= MAX_DEPTH:
            raise SchemaBuildError(f"{path}: nesting depth exceeds {MAX_DEPTH}")
        if node.get("additionalProperties", False) is not False:
            raise SchemaBuildError(f"{path}: additionalProperties must be false in strict mode")
        props = dict(node.get("properties", {}))
        counts["props"] += len(props)
        required = set(node.get("required", []))
        unknown = required - set(props)
        if unknown:
            raise SchemaBuildError(f"{path}: required names unknown properties {sorted(unknown)}")
        new_props = {}
        for name, sub in props.items():
            sub = _normalize(sub, depth=depth + 1, counts=counts, path=f"{path}.{name}")
            new_props[name] = sub if name in required else _nullable(sub)
        node["properties"] = new_props
        node["required"] = list(props)
        node["additionalProperties"] = False
    if "items" in node:
        node["items"] = _normalize(node["items"], depth=depth + 1, counts=counts, path=f"{path}[]")
    if "anyOf" in node:
        node["anyOf"] = [
            _normalize(s, depth=depth, counts=counts, path=f"{path}|{i}") for i, s in enumerate(node["anyOf"])
        ]
    return node


def _nullable(node: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in node:
        return {"anyOf": [node, {"type": "null"}]}
    if "anyOf" in node:
        if not any(s.get("type") == "null" for s in node["anyOf"]):
            node = {**node, "anyOf": [*node["anyOf"], {"type": "null"}]}
        return node
    t = node.get("type")
    if isinstance(t, str) and t != "null":
        node = {**node, "type": [t, "null"]}
    elif isinstance(t, list) and "null" not in t:
        node = {**node, "type": [*t, "null"]}
    if "enum" in node and None not in node["enum"]:
        node = {**node, "enum": [*node["enum"], None]}
    return node


# ------------------------------------------------------------------ validator

_PY_TYPES: dict[str, Callable[[Any], bool]] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate_instance(instance: Any, schema: Mapping[str, Any]) -> list[str]:
    """Validate against the subset of JSON Schema strict mode allows. Returns error strings."""
    errors: list[str] = []
    _validate(instance, schema, schema, "$", errors)
    return errors


def _resolve(ref: str, root: Mapping[str, Any]) -> Mapping[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaBuildError(f"only local refs are supported: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _validate(v: Any, s: Mapping[str, Any], root: Mapping[str, Any], path: str, errors: list[str]) -> None:
    if "$ref" in s:
        _validate(v, _resolve(s["$ref"], root), root, path, errors)
        return
    if "anyOf" in s:
        if not any(not validate_instance_in(v, sub, root) for sub in s["anyOf"]):
            errors.append(f"{path}: matches no anyOf branch")
        return
    if "const" in s and v != s["const"]:
        errors.append(f"{path}: expected const {s['const']!r}")
    if "enum" in s and not any(v == e and type(v) is type(e) for e in s["enum"]):
        errors.append(f"{path}: {v!r} not in enum")
        return
    t = s.get("type")
    if t is not None:
        allowed = t if isinstance(t, list) else [t]
        if not any(_PY_TYPES[a](v) for a in allowed):
            errors.append(f"{path}: expected {'/'.join(allowed)}, got {type(v).__name__}")
            return
    if isinstance(v, dict):
        props = s.get("properties", {})
        for name in s.get("required", []):
            if name not in v:
                errors.append(f"{path}: missing required '{name}'")
        if s.get("additionalProperties") is False:
            for name in v:
                if name not in props:
                    errors.append(f"{path}: unexpected property '{name}'")
        for name, sub in props.items():
            if name in v:
                _validate(v[name], sub, root, f"{path}.{name}", errors)
    elif isinstance(v, list):
        if "minItems" in s and len(v) < s["minItems"]:
            errors.append(f"{path}: fewer than {s['minItems']} items")
        if "maxItems" in s and len(v) > s["maxItems"]:
            errors.append(f"{path}: more than {s['maxItems']} items")
        if "items" in s:
            for i, item in enumerate(v):
                _validate(item, s["items"], root, f"{path}[{i}]", errors)
    elif isinstance(v, str):
        if "minLength" in s and len(v) < s["minLength"]:
            errors.append(f"{path}: shorter than {s['minLength']}")
        if "maxLength" in s and len(v) > s["maxLength"]:
            errors.append(f"{path}: longer than {s['maxLength']}")
        if "pattern" in s and not re.search(s["pattern"], v):
            errors.append(f"{path}: does not match pattern")
    elif isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in s and v < s["minimum"]:
            errors.append(f"{path}: below minimum {s['minimum']}")
        if "maximum" in s and v > s["maximum"]:
            errors.append(f"{path}: above maximum {s['maximum']}")


def validate_instance_in(v: Any, schema: Mapping[str, Any], root: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    _validate(v, schema, root, "$", errors)
    return errors


# ------------------------------------------------------------------ public wrapper


@dataclass(frozen=True)
class StructuredSchema:
    """A named strict schema with its validator. Pass to Provider.complete_json."""

    name: str
    json_schema: dict[str, Any]
    model: Any = field(default=None, compare=False)  # pydantic model class, if any
    # Top-level properties a reply may leave out (slice 6): strict mode still sends them to the model as
    # required, but a scripted reply or recording made before they were added is not rejected for lacking one;
    # the caller reads a missing one as empty. Every property that is present is still validated.
    tolerate_missing: frozenset[str] = field(default=frozenset(), compare=False)

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise SchemaBuildError(f"schema name must match {_NAME_RE.pattern}: {self.name!r}")

    @classmethod
    def from_dict(cls, name: str, schema: Mapping[str, Any]) -> "StructuredSchema":
        return cls(name, strict_json_schema(schema))

    @classmethod
    def from_model(cls, name: str, model: Any) -> "StructuredSchema":
        """Build from a pydantic v2 model class. Validation and parsing then go through pydantic too."""
        return cls(name, strict_json_schema(model.model_json_schema()), model)

    def response_format(self) -> dict[str, Any]:
        return {"type": "json_schema", "json_schema": {"name": self.name, "schema": self.json_schema, "strict": True}}

    def validate(self, data: Any) -> list[str]:
        errors = validate_instance(data, self.json_schema)
        if self.tolerate_missing:
            skip = {f"$: missing required '{name}'" for name in self.tolerate_missing}
            errors = [e for e in errors if e not in skip]
        if not errors and self.model is not None:
            try:
                self.model.model_validate(data)
            except Exception as e:  # pydantic.ValidationError, kept generic to avoid the import
                errors = [str(e)]
        return errors

    def parse(self, data: Any) -> Any:
        return self.model.model_validate(data) if self.model is not None else data


def fact_extraction_schema(fields: Sequence[str], *, name: str = "fact_extraction") -> StructuredSchema:
    """Schema for spec section 4 step 2: facts as {field, value, page, quote}.

    `value`, `page` and `quote` are nullable so a field absent from the document
    comes back null (M5). `options_detected` and `option_compared` carry D8's
    multi-option detection. T5 may use this or build its own with the same builder.
    """
    fields = list(fields)
    if not fields or len(set(fields)) != len(fields):
        raise SchemaBuildError("fact schema needs a non-empty list of unique field names")
    return StructuredSchema.from_dict(
        name,
        {
            "type": "object",
            "properties": {
                "facts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "enum": fields},
                            "value": {"type": ["string", "null"]},
                            "page": {"type": ["integer", "null"], "description": "1-based page in this document"},
                            "quote": {"type": ["string", "null"], "description": "verbatim text containing the value"},
                        },
                        "required": ["field", "value", "page", "quote"],
                    },
                },
                "options_detected": {"type": "integer", "description": "quote options found in this document"},
                "option_compared": {"type": ["string", "null"], "description": "name of the first option presented"},
            },
            "required": ["facts", "options_detected", "option_compared"],
        },
    )

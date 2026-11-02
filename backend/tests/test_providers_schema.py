"""Strict JSON-schema builder and local validator (T2)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from providers.errors import SchemaBuildError
from providers.schema import (
    StructuredSchema,
    fact_extraction_schema,
    strict_json_schema,
    validate_instance,
)


def test_every_object_gets_all_required_and_no_additional_properties():
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "inner": {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
            },
        },
        "required": ["a"],
    }
    out = strict_json_schema(schema)
    assert out["additionalProperties"] is False
    assert out["required"] == ["a", "inner"]
    assert out["properties"]["inner"]["additionalProperties"] is False
    assert out["properties"]["inner"]["required"] == ["x"]


def test_optional_fields_become_nullable_and_input_is_not_mutated():
    schema = {
        "type": "object",
        "properties": {
            "s": {"type": "string"},
            "multi": {"type": ["string", "integer"]},
            "e": {"enum": ["a", "b"]},
            "ref": {"$ref": "#/$defs/Thing"},
            "any": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            "req": {"type": "string"},
        },
        "required": ["req"],
        "$defs": {"Thing": {"type": "object", "properties": {"k": {"type": "string"}}}},
    }
    original = repr(schema)
    out = strict_json_schema(schema)
    props = out["properties"]
    assert props["s"]["type"] == ["string", "null"]
    assert props["multi"]["type"] == ["string", "integer", "null"]
    assert None in props["e"]["enum"]
    assert props["ref"] == {"anyOf": [{"$ref": "#/$defs/Thing"}, {"type": "null"}]}
    assert {"type": "null"} in props["any"]["anyOf"]
    assert props["req"]["type"] == "string"
    assert out["$defs"]["Thing"]["additionalProperties"] is False
    assert repr(schema) == original


def test_already_nullable_is_not_duplicated():
    schema = {
        "type": "object",
        "properties": {"x": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
    }
    out = strict_json_schema(schema)
    assert out["properties"]["x"]["anyOf"].count({"type": "null"}) == 1


def test_defaults_are_stripped():
    schema = {"type": "object", "properties": {"x": {"type": "string", "default": "y"}}}
    out = strict_json_schema(schema)
    assert "default" not in out["properties"]["x"]


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "array", "items": {"type": "string"}},
        {"anyOf": [{"type": "object", "properties": {}}]},
        {"type": "object", "properties": {"x": {"oneOf": [{"type": "string"}]}}},
        {"type": "object", "properties": {"x": {"allOf": [{"type": "string"}]}}},
        {"type": "object", "properties": {}, "patternProperties": {"^a": {"type": "string"}}},
        {"type": "object", "properties": {}, "additionalProperties": True},
        {"type": "object", "properties": {"x": {"not": {"type": "string"}}}},
    ],
)
def test_unsupported_shapes_are_rejected(bad):
    with pytest.raises(SchemaBuildError):
        strict_json_schema(bad)


def test_nesting_depth_limit():
    node: dict = {"type": "string"}
    for _ in range(11):
        node = {"type": "object", "properties": {"n": node}}
    with pytest.raises(SchemaBuildError, match="depth"):
        strict_json_schema(node)


def test_schema_name_validation():
    with pytest.raises(SchemaBuildError):
        StructuredSchema.from_dict("bad name!", {"type": "object", "properties": {}})


def test_response_format_shape():
    s = StructuredSchema.from_dict("facts_v1", {"type": "object", "properties": {"a": {"type": "string"}}})
    rf = s.response_format()
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "facts_v1"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["additionalProperties"] is False


class _Inner(BaseModel):
    k: int


class _Model(BaseModel):
    name: str
    note: str | None = None
    inner: _Inner


def test_from_pydantic_model_builds_strict_schema_and_parses():
    s = StructuredSchema.from_model("m", _Model)
    js = s.json_schema
    assert js["required"] == ["name", "note", "inner"]
    assert js["additionalProperties"] is False
    assert "default" not in js["properties"]["note"]
    parsed = s.parse({"name": "x", "note": None, "inner": {"k": 1}})
    assert isinstance(parsed, _Model)
    assert s.validate({"name": "x", "note": None, "inner": {"k": "nope"}})


def test_validator_accepts_and_rejects():
    schema = strict_json_schema(
        {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "minimum": 0},
                "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
                "kind": {"enum": ["a", "b"]},
                "ref": {"$ref": "#/$defs/T"},
            },
            "required": ["n", "tags", "kind", "ref"],
            "$defs": {"T": {"type": "object", "properties": {"z": {"type": "boolean"}}, "required": ["z"]}},
        }
    )
    good = {"n": 1, "tags": ["x"], "kind": "a", "ref": {"z": True}}
    assert validate_instance(good, schema) == []
    bad = {"n": -1, "tags": ["x", 2, "y"], "kind": "c", "ref": {"z": 1, "extra": 0}, "more": 1}
    errors = validate_instance(bad, schema)
    joined = "\n".join(errors)
    for needle in ["$.n", "$.tags[1]", "$.tags", "$.kind", "$.ref.z", "$.ref", "$"]:
        assert needle in joined
    assert validate_instance({"n": 1}, schema)  # missing required
    assert validate_instance({"n": True, "tags": [], "kind": "a", "ref": {"z": True}}, schema)  # bool is not int


def test_fact_extraction_schema_shape():
    s = fact_extraction_schema(["named_insured", "policy_term"])
    js = s.json_schema
    fact = js["properties"]["facts"]["items"]
    assert fact["properties"]["field"]["enum"] == ["named_insured", "policy_term"]
    assert set(fact["required"]) == {"field", "value", "page", "quote"}
    assert "options_detected" in js["properties"]
    ok = {
        "facts": [{"field": "named_insured", "value": "Acme", "page": 1, "quote": "Named Insured: Acme"}],
        "options_detected": 1,
        "option_compared": None,
    }
    assert s.validate(ok) == []
    assert s.validate({**ok, "facts": [{**ok["facts"][0], "field": "made_up"}]})
    with pytest.raises(SchemaBuildError):
        fact_extraction_schema([])

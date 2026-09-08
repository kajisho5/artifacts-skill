from __future__ import annotations

from artifact_skill.core.schema_validate import validate_against_schema


def test_empty_schema_accepts_anything():
    assert validate_against_schema({"anything": 1}, {}) == []
    assert validate_against_schema(None, {}) == []


def test_type_mismatch_is_reported():
    errors = validate_against_schema("not an object", {"type": "object"})
    assert len(errors) == 1
    assert "expected type 'object'" in errors[0]


def test_required_property_missing_is_reported():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    errors = validate_against_schema({}, schema)
    assert any("missing required property 'a'" in e for e in errors)


def test_required_property_present_passes():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    assert validate_against_schema({"a": "x"}, schema) == []


def test_additional_properties_false_rejects_unexpected_keys():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": False}
    errors = validate_against_schema({"a": "x", "b": "y"}, schema)
    assert any("unexpected property 'b'" in e for e in errors)


def test_additional_properties_unset_allows_extra_keys():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert validate_against_schema({"a": "x", "b": "y"}, schema) == []


def test_nested_property_type_is_checked():
    schema = {"type": "object", "properties": {"n": {"type": "number"}}}
    errors = validate_against_schema({"n": "not a number"}, schema)
    assert any("$.n" in e for e in errors)


def test_number_type_rejects_bool():
    """Python's bool is a subtype of int; a JSON Schema 'number' must not
    silently accept True/False."""
    errors = validate_against_schema({"n": True}, {"type": "object", "properties": {"n": {"type": "number"}}})
    assert errors


def test_integer_type_accepts_int_rejects_float():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    assert validate_against_schema({"n": 5}, schema) == []
    assert validate_against_schema({"n": 5.5}, schema) != []


def test_exclusive_minimum():
    schema = {"type": "object", "properties": {"n": {"type": "number", "exclusiveMinimum": 0}}}
    assert validate_against_schema({"n": 1}, schema) == []
    assert validate_against_schema({"n": 0}, schema) != []
    assert validate_against_schema({"n": -1}, schema) != []


def test_minimum_and_maximum():
    schema = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 10}}}
    assert validate_against_schema({"n": 1}, schema) == []
    assert validate_against_schema({"n": 10}, schema) == []
    assert validate_against_schema({"n": 0}, schema) != []
    assert validate_against_schema({"n": 11}, schema) != []


def test_array_min_items():
    schema = {"type": "object", "properties": {"xs": {"type": "array", "items": {"type": "string"}, "minItems": 1}}}
    assert validate_against_schema({"xs": ["a"]}, schema) == []
    assert validate_against_schema({"xs": []}, schema) != []


def test_array_item_type_is_checked():
    schema = {"type": "object", "properties": {"xs": {"type": "array", "items": {"type": "string"}}}}
    errors = validate_against_schema({"xs": ["a", 1, "c"]}, schema)
    assert len(errors) == 1
    assert "$.xs[1]" in errors[0]


def test_enum_rejects_value_not_in_list():
    schema = {"type": "object", "properties": {"mode": {"type": "string", "enum": ["a", "b"]}}}
    assert validate_against_schema({"mode": "a"}, schema) == []
    assert validate_against_schema({"mode": "c"}, schema) != []


def test_real_fit_page_size_schema_end_to_end():
    """The exact args_schema PdfAdapter.operations()['fit_page_size']
    declares — a regression guard for the shape actually used in
    production, not just synthetic schemas."""
    schema = {
        "type": "object",
        "properties": {
            "width_pt": {"type": "number", "exclusiveMinimum": 0},
            "height_pt": {"type": "number", "exclusiveMinimum": 0},
        },
        "required": ["width_pt", "height_pt"],
        "additionalProperties": False,
    }
    assert validate_against_schema({"width_pt": 612, "height_pt": 792}, schema) == []
    assert validate_against_schema({"width_pt": 612}, schema) != []  # missing required
    assert validate_against_schema({"width_pt": 0, "height_pt": 792}, schema) != []  # not > 0
    assert validate_against_schema({"width_pt": 612, "height_pt": 792, "extra": 1}, schema) != []


def test_nullable_type_accepts_null_or_the_named_type():
    schema = {"type": "object", "properties": {"n": {"type": ["string", "null"]}}}
    assert validate_against_schema({"n": "x"}, schema) == []
    assert validate_against_schema({"n": None}, schema) == []
    assert validate_against_schema({"n": 5}, schema) != []


def test_nullable_object_type_skips_nested_validation_when_null():
    schema = {
        "type": "object",
        "properties": {"sub": {"type": ["object", "null"], "properties": {"a": {"type": "string"}}, "required": ["a"]}},
    }
    assert validate_against_schema({"sub": None}, schema) == []
    assert validate_against_schema({"sub": {"a": "x"}}, schema) == []
    assert validate_against_schema({"sub": {}}, schema) != []  # missing required 'a'


def test_additional_properties_as_schema_validates_dynamic_keyed_map():
    schema = {
        "type": "object",
        "properties": {"fixed": {"type": "string"}},
        "additionalProperties": {"type": "integer"},
    }
    assert validate_against_schema({"fixed": "x", "a": 1, "b": 2}, schema) == []
    errors = validate_against_schema({"fixed": "x", "a": "not an int"}, schema)
    assert errors
    assert "$.a" in errors[0]


def test_additional_properties_as_schema_does_not_apply_to_declared_properties():
    schema = {
        "type": "object",
        "properties": {"fixed": {"type": "string"}},
        "additionalProperties": {"type": "integer"},
    }
    # 'fixed' is declared with its own schema; additionalProperties must not
    # also apply to it (it would wrongly reject a string against 'integer').
    assert validate_against_schema({"fixed": "x"}, schema) == []

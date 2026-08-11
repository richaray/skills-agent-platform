"""Tests for skill definition validation - the gate that stops a broken skill
from ever being published or run."""

from app import validation

VALID = {
    "instructions": "Look up the order, check the refund policy, and reply with a decision.",
    "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
    "output_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
    "examples": [],
    "allowed_tools": ["record_lookup"],
    "approval_required_tools": [],
    "max_steps": 8,
}


def fields_with_errors(definition):
    return {p.field for p in validation.validate_skill_definition(definition) if p.severity == "error"}


def test_a_good_definition_has_no_errors():
    problems = validation.validate_skill_definition(VALID)
    assert not validation.has_errors(problems)


def test_instructions_are_required():
    assert "instructions" in fields_with_errors({**VALID, "instructions": "   "})


def test_short_instructions_are_only_a_warning():
    problems = validation.validate_skill_definition({**VALID, "instructions": "do it"})
    assert not validation.has_errors(problems)
    assert any(p.severity == "warning" and p.field == "instructions" for p in problems)


def test_unknown_tools_are_rejected():
    assert "allowed_tools" in fields_with_errors(
        {**VALID, "allowed_tools": ["record_lookup", "hack_the_database"]}
    )


def test_write_tools_must_require_approval():
    """A user cannot opt out of approval for a tool that changes data."""
    assert "approval_required_tools" in fields_with_errors(
        {**VALID, "allowed_tools": ["create_task"], "approval_required_tools": []}
    )


def test_write_tool_with_approval_is_accepted():
    problems = validation.validate_skill_definition(
        {
            **VALID,
            "allowed_tools": ["create_task"],
            "approval_required_tools": ["create_task"],
        }
    )
    assert not validation.has_errors(problems)


def test_approval_list_cannot_name_a_tool_that_is_not_allowed():
    assert "approval_required_tools" in fields_with_errors(
        {**VALID, "allowed_tools": ["calculator"], "approval_required_tools": ["create_task"]}
    )


def test_step_limit_must_be_within_the_platform_cap():
    assert "max_steps" in fields_with_errors({**VALID, "max_steps": 500})
    assert "max_steps" in fields_with_errors({**VALID, "max_steps": 0})
    assert "max_steps" in fields_with_errors({**VALID, "max_steps": "eight"})


def test_a_malformed_json_schema_is_rejected():
    assert "input_schema" in fields_with_errors(
        {**VALID, "input_schema": {"type": "object", "properties": {"x": {"type": 12345}}}}
    )


def test_an_example_that_contradicts_the_input_schema_warns():
    problems = validation.validate_skill_definition(
        {
            **VALID,
            "input_schema": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
            "examples": [{"input": {"wrong_field": 1}, "output": {"answer": "x"}}],
        }
    )
    assert any(p.severity == "warning" and p.field.startswith("examples") for p in problems)


def test_examples_need_both_input_and_output():
    assert "examples[0]" in fields_with_errors(
        {**VALID, "examples": [{"input": {"order_id": "1"}}]}
    )


# --- run input validation ----------------------------------------------------


def test_input_matching_the_schema_passes():
    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    }
    assert validation.validate_input_against_schema({"order_id": "ORD-1"}, schema) is None


def test_input_of_the_wrong_type_is_rejected_with_a_readable_message():
    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    }
    message = validation.validate_input_against_schema({"order_id": 123}, schema)
    assert message is not None
    assert "order_id" in message


def test_missing_required_input_is_rejected():
    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    }
    assert validation.validate_input_against_schema({}, schema) is not None

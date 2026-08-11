"""
Skill definition validation.

A skill definition is user-written, so it can be wrong in many ways: a schema
that is not valid JSON Schema, a tool that does not exist, a step limit of a
million. This module is the single place that decides whether a definition is
usable.

It runs in two places:
  * when a draft is saved   - so the user sees problems immediately
  * when a draft is published - a definition can never become published while
    it still has errors

It returns a list of problems rather than raising on the first one, so the UI
can show everything that needs fixing at once.
"""

from dataclasses import dataclass

import jsonschema

from app.config import HARD_MAX_STEPS
from app.tools import TOOLS, all_tool_names


@dataclass
class Problem:
    """One thing wrong with a definition.

    `severity` is either "error" (blocks publishing) or "warning" (allowed, but
    probably a mistake).
    """

    field: str
    message: str
    severity: str = "error"

    def as_dict(self) -> dict:
        return {"field": self.field, "message": self.message, "severity": self.severity}


def _check_json_schema(schema: object, field: str) -> list[Problem]:
    """Confirms something really is a usable JSON Schema."""
    problems: list[Problem] = []

    if not isinstance(schema, dict):
        return [Problem(field, "Must be a JSON object.")]

    if not schema:
        return [Problem(field, "Must not be empty.", severity="warning")]

    try:
        # This validates the schema itself, not data against it.
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        problems.append(Problem(field, f"Not a valid JSON Schema: {exc.message}"))

    if schema.get("type") != "object":
        problems.append(
            Problem(
                field,
                "Should have \"type\": \"object\" so inputs and outputs are named fields.",
                severity="warning",
            )
        )

    return problems


def validate_skill_definition(definition: dict) -> list[Problem]:
    """Checks a whole skill definition. Empty list means it is good to publish."""
    problems: list[Problem] = []

    # --- instructions ---
    instructions = (definition.get("instructions") or "").strip()
    if not instructions:
        problems.append(Problem("instructions", "Instructions are required."))
    elif len(instructions) < 20:
        problems.append(
            Problem(
                "instructions",
                "Very short instructions usually produce unreliable results.",
                severity="warning",
            )
        )

    # --- schemas ---
    problems += _check_json_schema(definition.get("input_schema"), "input_schema")
    problems += _check_json_schema(definition.get("output_schema"), "output_schema")

    # --- tools ---
    allowed = definition.get("allowed_tools")
    if not isinstance(allowed, list):
        problems.append(Problem("allowed_tools", "Must be a list of tool names."))
        allowed = []
    else:
        for name in allowed:
            if name not in TOOLS:
                problems.append(
                    Problem(
                        "allowed_tools",
                        f"'{name}' is not a real tool. Available: {', '.join(all_tool_names())}.",
                    )
                )

    # --- approval list ---
    needs_approval = definition.get("approval_required_tools")
    if not isinstance(needs_approval, list):
        problems.append(Problem("approval_required_tools", "Must be a list of tool names."))
        needs_approval = []
    else:
        for name in needs_approval:
            if name not in allowed:
                problems.append(
                    Problem(
                        "approval_required_tools",
                        f"'{name}' needs approval but is not in allowed_tools.",
                    )
                )

    # Platform policy: any tool that changes data must require approval. A user
    # cannot opt out of this, which is why it is checked here and not in the UI.
    for name in allowed:
        tool = TOOLS.get(name)
        if tool is not None and tool.is_write and name not in needs_approval:
            problems.append(
                Problem(
                    "approval_required_tools",
                    f"'{name}' changes data, so it must require approval.",
                )
            )

    # --- step limit ---
    max_steps = definition.get("max_steps")
    if not isinstance(max_steps, int) or isinstance(max_steps, bool):
        problems.append(Problem("max_steps", "Must be a whole number."))
    elif max_steps < 1:
        problems.append(Problem("max_steps", "Must be at least 1."))
    elif max_steps > HARD_MAX_STEPS:
        problems.append(
            Problem("max_steps", f"The platform limit is {HARD_MAX_STEPS} steps.")
        )

    # --- examples ---
    examples = definition.get("examples")
    if examples is None:
        examples = []
    if not isinstance(examples, list):
        problems.append(Problem("examples", "Must be a list."))
    else:
        input_schema = definition.get("input_schema")
        for index, example in enumerate(examples):
            label = f"examples[{index}]"

            if not isinstance(example, dict):
                problems.append(Problem(label, "Each example must be an object."))
                continue

            if "input" not in example or "output" not in example:
                problems.append(Problem(label, "Each example needs an 'input' and an 'output'."))
                continue

            # An example that does not match the skill's own input schema is a
            # strong sign one of the two is wrong.
            if isinstance(input_schema, dict) and input_schema:
                try:
                    jsonschema.validate(instance=example["input"], schema=input_schema)
                except jsonschema.ValidationError as exc:
                    problems.append(
                        Problem(
                            label,
                            f"Example input does not match input_schema: {exc.message}",
                            severity="warning",
                        )
                    )
                except jsonschema.SchemaError:
                    pass  # already reported by _check_json_schema

    return problems


def has_errors(problems: list[Problem]) -> bool:
    """Warnings are fine. Errors block publishing."""
    return any(p.severity == "error" for p in problems)


def validate_input_against_schema(input_data: dict, input_schema: dict) -> str | None:
    """Checks a run's input before we spend an API call on it.

    Returns an error message, or None if the input is fine.
    """
    if not input_schema:
        return None

    try:
        jsonschema.validate(instance=input_data, schema=input_schema)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(p) for p in exc.absolute_path) or "(root)"
        return f"Input is not valid at '{location}': {exc.message}"
    except jsonschema.SchemaError as exc:
        return f"This skill's input schema is itself invalid: {exc.message}"

    return None

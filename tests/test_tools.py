"""Tests for the four tools, with an emphasis on the calculator's safety."""

import pytest

from app import models, tools


# --- calculator --------------------------------------------------------------


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("2 + 2", 4),
        ("18000 * 0.9", 16200),
        ("(120 * 3) / 4", 90),
        ("2 ** 8", 256),
        ("-5 + 10", 5),
        ("17 % 5", 2),
    ],
)
def test_calculator_evaluates_arithmetic(db, expression, expected):
    result = tools.run_calculator(db, {"expression": expression}, {})
    assert result["result"] == expected


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo hacked')",
        "open('/etc/passwd').read()",
        "[].__class__.__base__.__subclasses__()",
        "exec('x=1')",
        "lambda: 1",
    ],
)
def test_calculator_refuses_anything_that_is_not_arithmetic(db, expression):
    """The calculator parses to a syntax tree and allows only arithmetic nodes.

    Using Python's eval() here would be a remote code execution hole, because
    the expression comes from a language model.
    """
    with pytest.raises(tools.ToolError):
        tools.run_calculator(db, {"expression": expression}, {})


def test_calculator_reports_division_by_zero(db):
    with pytest.raises(tools.ToolError, match="Division by zero"):
        tools.run_calculator(db, {"expression": "1/0"}, {})


def test_calculator_rejects_an_empty_expression(db):
    with pytest.raises(tools.ToolError):
        tools.run_calculator(db, {"expression": "   "}, {})


# --- document search ---------------------------------------------------------


def test_document_search_finds_matches(db):
    db.add(models.Document(title="Refund Policy", content="Refunds within 30 days."))
    db.add(models.Document(title="Shipping", content="Delivery takes 3-7 days."))
    db.commit()

    result = tools.run_document_search(db, {"query": "refund"}, {})

    assert result["match_count"] == 1
    assert result["results"][0]["title"] == "Refund Policy"


def test_document_search_returns_an_empty_result_rather_than_failing(db):
    """Finding nothing is information, not an error - the model can react to it."""
    result = tools.run_document_search(db, {"query": "nothing matches this"}, {})
    assert result["match_count"] == 0
    assert result["results"] == []


# --- record lookup -----------------------------------------------------------


def test_record_lookup_returns_the_record(db):
    db.add(models.Record(record_type="order", external_id="ORD-1", data={"amount": 500}))
    db.commit()

    result = tools.run_record_lookup(db, {"record_type": "order", "external_id": "ORD-1"}, {})

    assert result["found"] is True
    assert result["data"]["amount"] == 500


def test_record_lookup_reports_a_missing_record_as_data(db):
    result = tools.run_record_lookup(db, {"record_type": "order", "external_id": "NOPE"}, {})
    assert result["found"] is False


def test_record_lookup_requires_both_arguments(db):
    with pytest.raises(tools.ToolError):
        tools.run_record_lookup(db, {"record_type": "order"}, {})


# --- create task (the write tool) --------------------------------------------


def test_create_task_writes_a_task(db):
    result = tools.run_create_task(
        db, {"title": "Follow up", "assignee": "sam"}, {"execution_id": None}
    )
    db.commit()

    assert result["already_existed"] is False
    assert db.query(models.Task).count() == 1


def test_create_task_is_idempotent(db):
    """Calling the tool twice with the same key returns the first task."""
    context = {"execution_id": None, "idempotency_key": "same-key"}

    first = tools.run_create_task(db, {"title": "Once"}, context)
    db.commit()
    second = tools.run_create_task(db, {"title": "Once"}, context)
    db.commit()

    assert first["already_existed"] is False
    assert second["already_existed"] is True
    assert second["task_id"] == first["task_id"]
    assert db.query(models.Task).count() == 1


def test_create_task_requires_a_title(db):
    with pytest.raises(tools.ToolError):
        tools.run_create_task(db, {"description": "no title"}, {})


# --- the registry ------------------------------------------------------------


def test_only_create_task_writes_data():
    """If this ever changes, the approval rules must be revisited."""
    assert tools.write_tool_names() == ["create_task"]


def test_unknown_tools_are_not_resolvable():
    assert tools.get_tool("definitely_not_a_tool") is None

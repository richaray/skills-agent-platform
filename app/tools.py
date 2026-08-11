"""
The bounded set of tools the agent can use.

Four tools, three read-only and one write:

    calculator       evaluate an arithmetic expression      (read)
    document_search  keyword search over the documents table (read)
    record_lookup    fetch one structured record by id       (read)
    create_task      create a task row                       (WRITE - needs approval)

Two rules the rest of the app depends on:

  1. A tool that is not in this registry does not exist. If the model asks for
     one, we refuse and tell it so.
  2. A tool marked `is_write` can never run without a human approving it first.
     That check lives in agent.py, but the flag is declared here.
"""

import ast
import operator
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from app import models


class ToolError(Exception):
    """Raised when a tool fails in an expected way (bad input, nothing found).

    The agent catches this, records it as a failed step, and lets the model try
    something else. It is not a crash.
    """


@dataclass
class Tool:
    name: str
    description: str
    # JSON Schema for this tool's arguments. Sent to the model so it knows how
    # to call the tool, and used by us to validate what comes back.
    parameters: dict
    is_write: bool
    handler: Callable[..., dict]


# --- calculator --------------------------------------------------------------

# Only these operations are allowed. Anything else in the expression is rejected.
_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float:
    """Walks the parsed expression and computes it.

    We deliberately do NOT use Python's built-in eval(). eval would happily run
    any code the model produced, which would be a remote code execution hole.
    Instead we parse to a syntax tree and allow only arithmetic nodes.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ToolError("Only numbers are allowed in expressions.")

    if isinstance(node, ast.BinOp):
        op = _ALLOWED_OPERATORS.get(type(node.op))
        if op is None:
            raise ToolError(f"Operator {type(node.op).__name__} is not allowed.")
        return op(_safe_eval(node.left), _safe_eval(node.right))

    if isinstance(node, ast.UnaryOp):
        op = _ALLOWED_OPERATORS.get(type(node.op))
        if op is None:
            raise ToolError(f"Operator {type(node.op).__name__} is not allowed.")
        return op(_safe_eval(node.operand))

    raise ToolError("Expression contains something that is not simple arithmetic.")


def run_calculator(db: Session, args: dict, context: dict) -> dict:
    expression = str(args.get("expression", "")).strip()
    if not expression:
        raise ToolError("No expression was provided.")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"Could not parse the expression: {exc}") from exc

    try:
        result = _safe_eval(tree.body)
    except ZeroDivisionError as exc:
        raise ToolError("Division by zero.") from exc

    return {"expression": expression, "result": result}


# --- document_search ---------------------------------------------------------


def run_document_search(db: Session, args: dict, context: dict) -> dict:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ToolError("No search query was provided.")

    limit = int(args.get("limit", 3))
    limit = max(1, min(limit, 10))

    # A simple case-insensitive keyword match. Good enough for a bounded demo
    # corpus; a real system would use full-text search or embeddings. This is
    # noted in the README as a deliberate simplification.
    pattern = f"%{query}%"
    rows = (
        db.query(models.Document)
        .filter(
            models.Document.title.ilike(pattern) | models.Document.content.ilike(pattern)
        )
        .limit(limit)
        .all()
    )

    return {
        "query": query,
        "match_count": len(rows),
        "results": [
            {"id": r.id, "title": r.title, "content": r.content} for r in rows
        ],
    }


# --- record_lookup -----------------------------------------------------------


def run_record_lookup(db: Session, args: dict, context: dict) -> dict:
    record_type = str(args.get("record_type", "")).strip()
    external_id = str(args.get("external_id", "")).strip()

    if not record_type or not external_id:
        raise ToolError("Both record_type and external_id are required.")

    row = (
        db.query(models.Record)
        .filter(
            models.Record.record_type == record_type,
            models.Record.external_id == external_id,
        )
        .first()
    )

    if row is None:
        # Not finding a record is a normal outcome, not a crash. We return it as
        # data so the model can decide what to do next.
        return {
            "found": False,
            "record_type": record_type,
            "external_id": external_id,
        }

    return {
        "found": True,
        "record_type": row.record_type,
        "external_id": row.external_id,
        "data": row.data,
    }


# --- create_task (the write action) ------------------------------------------


def run_create_task(db: Session, args: dict, context: dict) -> dict:
    """Creates a task row.

    This is the only tool that changes data, so it is the one that requires
    approval. `idempotency_key` comes from the approval record, and the unique
    constraint on the tasks table is the last line of defence against the same
    approved action being run twice.
    """
    title = str(args.get("title", "")).strip()
    if not title:
        raise ToolError("A task must have a title.")

    idempotency_key = context.get("idempotency_key")

    # If a task with this key already exists, return it rather than making a
    # second one. This makes the tool safe to call again after a timeout.
    if idempotency_key:
        existing = (
            db.query(models.Task)
            .filter(models.Task.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None:
            return {
                "task_id": existing.id,
                "title": existing.title,
                "assignee": existing.assignee,
                "already_existed": True,
            }

    task = models.Task(
        title=title,
        description=str(args.get("description", "")).strip(),
        assignee=str(args.get("assignee", "unassigned")).strip() or "unassigned",
        created_by_execution_id=context.get("execution_id"),
        idempotency_key=idempotency_key,
    )
    db.add(task)
    db.flush()  # assigns task.id without ending the caller's transaction

    return {
        "task_id": task.id,
        "title": task.title,
        "assignee": task.assignee,
        "already_existed": False,
    }


# --- the registry ------------------------------------------------------------

TOOLS: dict[str, Tool] = {
    "calculator": Tool(
        name="calculator",
        description=(
            "Evaluate a basic arithmetic expression such as '(120 * 3) / 4'. "
            "Supports + - * / % and exponent. Use this instead of doing mental maths."
        ),
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The arithmetic expression to evaluate.",
                }
            },
            "required": ["expression"],
        },
        is_write=False,
        handler=run_calculator,
    ),
    "document_search": Tool(
        name="document_search",
        description=(
            "Keyword search over the internal company document library. "
            "Returns matching documents with their full text."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords to search for."},
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of documents to return (1-10).",
                },
            },
            "required": ["query"],
        },
        is_write=False,
        handler=run_document_search,
    ),
    "record_lookup": Tool(
        name="record_lookup",
        description=(
            "Look up one structured business record by its type and id, for "
            "example record_type='order', external_id='ORD-1002'. "
            "Available types: customer, order, invoice."
        ),
        parameters={
            "type": "object",
            "properties": {
                "record_type": {
                    "type": "string",
                    "description": "One of: customer, order, invoice.",
                },
                "external_id": {
                    "type": "string",
                    "description": "The record's id, e.g. 'CUST-001' or 'ORD-1002'.",
                },
            },
            "required": ["record_type", "external_id"],
        },
        is_write=False,
        handler=run_record_lookup,
    ),
    "create_task": Tool(
        name="create_task",
        description=(
            "Create a task in the task tracker. This changes data and always "
            "requires human approval before it runs."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short task title."},
                "description": {
                    "type": "string",
                    "description": "What needs to be done and why.",
                },
                "assignee": {
                    "type": "string",
                    "description": "Who the task is for. Defaults to 'unassigned'.",
                },
            },
            "required": ["title"],
        },
        is_write=True,
        handler=run_create_task,
    ),
}


def get_tool(name: str) -> Tool | None:
    """Returns the tool, or None if no such tool exists."""
    return TOOLS.get(name)


def all_tool_names() -> list[str]:
    return sorted(TOOLS.keys())


def write_tool_names() -> list[str]:
    return sorted(name for name, tool in TOOLS.items() if tool.is_write)

"""
The shapes that cross the API boundary.

Two halves, on purpose:

  * Incoming requests use Pydantic models, so FastAPI rejects malformed JSON
    before any of our code runs.
  * Outgoing responses use plain functions that build dictionaries. This is more
    typing than Pydantic's ORM mode, but you can read one function and know
    exactly what the frontend receives - no hidden field mapping.
"""

from typing import Any

from pydantic import BaseModel, Field

from app import models


# --- incoming ----------------------------------------------------------------


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(default="", max_length=2000)


class SkillDefinitionInput(BaseModel):
    """The editable body of a draft version."""

    instructions: str = ""
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    examples: list[dict] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    approval_required_tools: list[str] = Field(default_factory=list)
    max_steps: int = 8


class RunRequest(BaseModel):
    input_data: dict = Field(default_factory=dict)


class RejectRequest(BaseModel):
    reason: str = ""


# --- outgoing ----------------------------------------------------------------


def skill_to_dict(skill: models.Skill) -> dict[str, Any]:
    published = [v for v in skill.versions if v.status == "published"]
    drafts = [v for v in skill.versions if v.status == "draft"]

    return {
        "id": skill.id,
        "name": skill.name,
        "purpose": skill.purpose,
        "created_at": skill.created_at,
        "version_count": len(skill.versions),
        "latest_published_version": max((v.version_number for v in published), default=None),
        "has_draft": len(drafts) > 0,
    }


def version_to_dict(version: models.SkillVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "skill_id": version.skill_id,
        "skill_name": version.skill.name if version.skill else None,
        "version_number": version.version_number,
        "status": version.status,
        "instructions": version.instructions,
        "input_schema": version.input_schema,
        "output_schema": version.output_schema,
        "examples": version.examples,
        "allowed_tools": version.allowed_tools,
        "approval_required_tools": version.approval_required_tools,
        "max_steps": version.max_steps,
        "created_at": version.created_at,
        "published_at": version.published_at,
        "is_editable": version.is_editable,
    }


def step_to_dict(step: models.ExecutionStep) -> dict[str, Any]:
    return {
        "id": step.id,
        "step_number": step.step_number,
        "kind": step.kind,
        "llm_text": step.llm_text,
        "tool_name": step.tool_name,
        "tool_input": step.tool_input,
        "tool_output": step.tool_output,
        "error_message": step.error_message,
        "duration_ms": step.duration_ms,
        "created_at": step.created_at,
    }


def approval_to_dict(approval: models.ApprovalRequest) -> dict[str, Any]:
    return {
        "id": approval.id,
        "execution_id": approval.execution_id,
        "step_number": approval.step_number,
        "tool_name": approval.tool_name,
        "tool_input": approval.tool_input,
        "status": approval.status,
        "executed": approval.executed,
        "tool_output": approval.tool_output,
        "idempotency_key": approval.idempotency_key,
        "created_at": approval.created_at,
        "decided_at": approval.decided_at,
    }


def execution_to_dict(execution: models.Execution, include_steps: bool = True) -> dict[str, Any]:
    version = execution.skill_version

    payload: dict[str, Any] = {
        "id": execution.id,
        "status": execution.status,
        "skill_version_id": execution.skill_version_id,
        "skill_id": version.skill_id if version else None,
        "skill_name": version.skill.name if version and version.skill else None,
        "version_number": version.version_number if version else None,
        "input_data": execution.input_data,
        "final_output": execution.final_output,
        "error_message": execution.error_message,
        "step_count": execution.step_count,
        "max_steps": version.max_steps if version else None,
        "rerun_of_execution_id": execution.rerun_of_execution_id,
        "created_at": execution.created_at,
        "finished_at": execution.finished_at,
    }

    if include_steps:
        payload["steps"] = [step_to_dict(s) for s in execution.steps]
        payload["approvals"] = [approval_to_dict(a) for a in execution.approvals]
        # The approval the UI should be asking about right now, if any.
        pending = next((a for a in execution.approvals if a.status == "pending"), None)
        payload["pending_approval"] = approval_to_dict(pending) if pending else None

    return payload

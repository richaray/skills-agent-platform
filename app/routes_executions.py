"""
API routes for running skills, approving write actions, and reading history.

A note on the execution model: runs are synchronous. A POST to /run blocks
until the run finishes or pauses for approval, then returns the whole thing.

That is a deliberate trade-off. It keeps the system easy to reason about and
easy to debug - no background workers, no queues, no polling - at the cost of
a slower HTTP response. Because every step is committed as it happens, a
cancel request from another browser tab is still picked up mid-run.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import agent, config, models, schemas, validation
from app.db import get_db
from app.logging_setup import app_log, log_event

router = APIRouter(prefix="/api", tags=["executions"])


def _get_execution(db: Session, execution_id: int) -> models.Execution:
    execution = db.get(models.Execution, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="That run does not exist.")
    return execution


def _get_approval(db: Session, approval_id: int) -> models.ApprovalRequest:
    approval = db.get(models.ApprovalRequest, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="That approval request does not exist.")
    return approval


def _start_run(
    db: Session,
    version: models.SkillVersion,
    input_data: dict,
    rerun_of: int | None = None,
) -> models.Execution:
    """Shared by /run and /rerun: check everything, then drive the loop."""

    # Fail early with a clear message rather than deep inside the agent loop.
    if not config.llm_is_configured():
        raise HTTPException(
            status_code=503,
            detail="The AI provider is not configured on this server (no API key). "
            "Runs are unavailable until it is set.",
        )

    # A broken definition must not be runnable, even as a draft test.
    problems = validation.validate_skill_definition(
        {
            "instructions": version.instructions,
            "input_schema": version.input_schema,
            "output_schema": version.output_schema,
            "examples": version.examples,
            "allowed_tools": version.allowed_tools,
            "approval_required_tools": version.approval_required_tools,
            "max_steps": version.max_steps,
        }
    )
    if validation.has_errors(problems):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "This skill definition has errors and cannot be run.",
                "problems": [p.as_dict() for p in problems],
            },
        )

    # Check the caller's input against the skill's own input schema before we
    # spend an API call on it.
    input_error = validation.validate_input_against_schema(input_data, version.input_schema or {})
    if input_error:
        raise HTTPException(status_code=422, detail={"message": input_error, "problems": []})

    execution = models.Execution(
        skill_version_id=version.id,
        status="running",
        input_data=input_data,
        rerun_of_execution_id=rerun_of,
    )
    db.add(execution)
    db.commit()
    db.refresh(execution)

    log_event(
        app_log,
        "execution_started",
        execution_id=execution.id,
        skill_id=version.skill_id,
        version_number=version.version_number,
        rerun_of=rerun_of,
    )

    execution = agent.run_execution(db, execution)
    return execution


@router.post("/versions/{version_id}/run")
def run_version(version_id: int, payload: schemas.RunRequest, db: Session = Depends(get_db)):
    """Runs a skill version with the given input. Works on drafts too, which is
    how you test a skill before publishing it."""
    version = db.get(models.SkillVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="That skill version does not exist.")

    execution = _start_run(db, version, payload.input_data)
    return schemas.execution_to_dict(execution)


@router.post("/executions/{execution_id}/rerun")
def rerun_execution(execution_id: int, db: Session = Depends(get_db)):
    """Runs the same version again with the same input.

    This is what makes old versions useful: because a published version is
    frozen, rerunning one reproduces the exact definition that was used before.
    The result can still differ - the model is not deterministic - which is
    precisely why keeping both runs in history is worth doing.
    """
    original = _get_execution(db, execution_id)

    execution = _start_run(
        db,
        original.skill_version,
        original.input_data or {},
        rerun_of=original.id,
    )
    return schemas.execution_to_dict(execution)


@router.get("/executions")
def list_executions(
    skill_id: int | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Run history, newest first. Steps are left out to keep the list light."""
    query = db.query(models.Execution)

    if skill_id is not None:
        query = query.join(models.SkillVersion).filter(models.SkillVersion.skill_id == skill_id)

    if status is not None:
        if status not in models.EXECUTION_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown status. Valid values: {', '.join(models.EXECUTION_STATUSES)}.",
            )
        query = query.filter(models.Execution.status == status)

    executions = query.order_by(models.Execution.created_at.desc()).limit(limit).all()
    return [schemas.execution_to_dict(e, include_steps=False) for e in executions]


@router.get("/executions/{execution_id}")
def get_execution(execution_id: int, db: Session = Depends(get_db)):
    """One run in full: every step, every tool call, every approval."""
    execution = _get_execution(db, execution_id)
    return schemas.execution_to_dict(execution)


# --- approvals ---------------------------------------------------------------


@router.get("/approvals")
def list_approvals(status: str = "pending", db: Session = Depends(get_db)):
    """Everything currently waiting on a human. Drives the approvals inbox."""
    if status not in models.APPROVAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown status. Valid values: {', '.join(models.APPROVAL_STATUSES)}.",
        )

    approvals = (
        db.query(models.ApprovalRequest)
        .filter(models.ApprovalRequest.status == status)
        .order_by(models.ApprovalRequest.created_at.desc())
        .all()
    )

    results = []
    for approval in approvals:
        entry = schemas.approval_to_dict(approval)
        version = approval.execution.skill_version
        entry["skill_name"] = version.skill.name if version and version.skill else None
        entry["version_number"] = version.version_number if version else None
        results.append(entry)

    return results


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: int, db: Session = Depends(get_db)):
    """Approves a paused write action and resumes the run.

    Calling this twice is safe. The second call finds `executed` already true
    and returns the run unchanged rather than performing the write again.
    """
    approval = _get_approval(db, approval_id)

    if approval.execution.status == "cancelled":
        raise HTTPException(status_code=409, detail="This run was cancelled.")

    try:
        execution = agent.approve_and_continue(db, approval)
    except agent.AgentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return schemas.execution_to_dict(execution)


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: int, payload: schemas.RejectRequest, db: Session = Depends(get_db)):
    """Rejects a paused write action. The run continues without it, and the
    model is told the action was refused."""
    approval = _get_approval(db, approval_id)

    try:
        execution = agent.reject_approval(db, approval, payload.reason)
    except agent.AgentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return schemas.execution_to_dict(execution)


@router.post("/executions/{execution_id}/cancel")
def cancel(execution_id: int, db: Session = Depends(get_db)):
    execution = _get_execution(db, execution_id)

    if execution.status in ("completed", "failed"):
        raise HTTPException(
            status_code=409, detail=f"This run has already {execution.status} and cannot be cancelled."
        )

    execution = agent.cancel_execution(db, execution)
    return schemas.execution_to_dict(execution)


# --- the data the tools touch ------------------------------------------------


@router.get("/tasks")
def list_tasks(db: Session = Depends(get_db)):
    """Tasks created by the create_task tool.

    Worth having in the UI: it is the proof that an approved write action
    actually happened, and that a rejected or duplicated one did not.
    """
    tasks = db.query(models.Task).order_by(models.Task.created_at.desc()).limit(100).all()
    return [
        {
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "assignee": t.assignee,
            "created_by_execution_id": t.created_by_execution_id,
            "created_at": t.created_at,
        }
        for t in tasks
    ]

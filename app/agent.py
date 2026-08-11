"""
The agent loop.

This is the core of the platform. One run of a skill works like this:

    1. Check we have not been cancelled and have steps left.
    2. Ask the model what to do next, telling it only about the tools this
       skill is allowed to use.
    3. If it asked for a tool:
         - unknown or not allowed  -> refuse, tell the model, keep going
         - needs approval          -> PAUSE and wait for a human
         - otherwise               -> run it, feed the result back, loop
    4. If it gave a final answer -> validate it against the skill's output
       schema and finish.

Two design decisions worth knowing:

  * Every step is written to the database as it happens, and we commit each
    one. That is what makes a run auditable, resumable after an approval, and
    cancellable while it is still going.

  * The conversation sent to the model is rebuilt from those database rows
    every time (see `build_contents`). We never hold conversation state in
    memory, so resuming a paused run days later works exactly the same as
    continuing one that never stopped.
"""

import hashlib
import json
import time
from typing import Any

import jsonschema
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models, tools
from app.config import HARD_MAX_STEPS
from app.llm import LLMError, call_llm
from app.logging_setup import agent_log, log_event
from app.models import utcnow


class AgentError(Exception):
    """Something went wrong that should stop the run."""


# --- building the prompt -----------------------------------------------------


def build_system_instruction(version: models.SkillVersion) -> str:
    """Turns a skill definition into the instruction the model receives."""
    allowed = version.allowed_tools or []
    needs_approval = version.approval_required_tools or []

    parts = [
        "You are executing a predefined skill. Follow its instructions exactly.",
        "",
        "## Skill instructions",
        version.instructions or "(none provided)",
        "",
        "## Required output format",
        "When you have finished, reply with ONLY a JSON object matching this schema.",
        "Do not wrap it in explanation text.",
        json.dumps(version.output_schema or {}, indent=2),
    ]

    if version.examples:
        parts += ["", "## Examples", json.dumps(version.examples, indent=2)]

    if allowed:
        parts += [
            "",
            "## Tools",
            "You may use ONLY these tools: " + ", ".join(allowed) + ".",
            "If you need something else, say so in your final answer instead of "
            "inventing a tool.",
        ]
        if needs_approval:
            parts += [
                "These tools change data and a human must approve them before "
                "they run: " + ", ".join(needs_approval) + ".",
            ]
    else:
        parts += ["", "## Tools", "You have no tools. Answer from the input alone."]

    parts += [
        "",
        "## Rules",
        "- Use a tool when it would give you a fact you do not have. Do not guess.",
        "- Do not claim an action succeeded unless a tool result confirmed it.",
        "- If information is missing, say so in your answer rather than inventing it.",
    ]

    return "\n".join(parts)


def build_contents(execution: models.Execution) -> list[dict]:
    """Rebuilds the model conversation from the steps stored in the database.

    Returns the list of 'contents' entries Gemini expects: the original input,
    then one model turn + one tool-result turn for each tool call so far.
    """
    contents: list[dict] = [
        {
            "role": "user",
            "parts": [
                {
                    "text": (
                        "Here is the input for this run:\n"
                        + json.dumps(execution.input_data, indent=2)
                        + "\n\nComplete the skill and return the required JSON."
                    )
                }
            ],
        }
    ]

    for step in execution.steps:
        if step.kind != "tool_call":
            continue

        # What the model asked for.
        contents.append(
            {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {
                            "name": step.tool_name,
                            "args": step.tool_input or {},
                        }
                    }
                ],
            }
        )

        # What actually happened. Errors and refusals go back to the model as
        # data, so it can recover instead of the whole run dying.
        if step.error_message:
            result: dict[str, Any] = {"error": step.error_message}
        elif step.tool_output is None:
            result = {"status": "pending approval from a human"}
        else:
            result = step.tool_output

        contents.append(
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": step.tool_name,
                            "response": result,
                        }
                    }
                ],
            }
        )

    return contents


# --- small helpers -----------------------------------------------------------


def make_idempotency_key(execution_id: int, step_number: int, tool_name: str, args: dict) -> str:
    """A stable fingerprint for one specific write action.

    Same execution + same step + same tool + same arguments always produces the
    same key. The unique constraint on that column is what stops the same
    approved write from being carried out twice.
    """
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"exec:{execution_id}:step:{step_number}:{tool_name}:{digest}"


def _extract_json(text: str) -> dict | None:
    """Pulls a JSON object out of the model's final message.

    Models often wrap JSON in ```json fences or add a sentence around it, so we
    strip fences and fall back to taking the outermost { ... } block.
    """
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        pass

    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    return None


def _record_step(db: Session, execution: models.Execution, **fields) -> models.ExecutionStep:
    """Adds one step row, bumps the counter, and commits straight away."""
    execution.step_count += 1
    step = models.ExecutionStep(
        execution_id=execution.id,
        step_number=execution.step_count,
        **fields,
    )
    db.add(step)
    db.commit()
    db.refresh(execution)
    return step


def _finish(
    db: Session,
    execution: models.Execution,
    status: str,
    final_output: dict | None = None,
    error_message: str | None = None,
) -> models.Execution:
    execution.status = status
    execution.final_output = final_output
    execution.error_message = error_message
    execution.finished_at = utcnow()
    db.commit()
    db.refresh(execution)

    log_event(
        agent_log,
        "execution_finished",
        execution_id=execution.id,
        status=status,
        steps_used=execution.step_count,
    )
    return execution


# --- running a tool ----------------------------------------------------------


def run_tool_now(
    db: Session,
    execution: models.Execution,
    tool: tools.Tool,
    args: dict,
) -> tuple[dict | None, str | None]:
    """Executes a tool and returns (output, error_message). Never raises.

    Tool failures are expected events, not crashes: we hand the error back to
    the model so it can try a different approach.
    """
    context = {
        "execution_id": execution.id,
        "idempotency_key": make_idempotency_key(
            execution.id, execution.step_count + 1, tool.name, args
        ),
    }

    try:
        output = tool.handler(db, args, context)
        db.commit()
        return output, None
    except tools.ToolError as exc:
        db.rollback()
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - a tool bug must not kill the run
        db.rollback()
        log_event(
            agent_log,
            "tool_unexpected_error",
            execution_id=execution.id,
            tool=tool.name,
            error=str(exc),
        )
        return None, f"The tool failed unexpectedly: {exc}"


# --- the loop ----------------------------------------------------------------


def run_execution(db: Session, execution: models.Execution) -> models.Execution:
    """Drives the run until it finishes, fails, pauses for approval, or is
    cancelled. Safe to call again to resume a paused run."""

    version = execution.skill_version
    allowed = list(version.allowed_tools or [])
    needs_approval = set(version.approval_required_tools or [])

    # A skill cannot ask for more steps than the platform allows.
    max_steps = min(version.max_steps or 8, HARD_MAX_STEPS)

    execution.status = "running"
    db.commit()

    while True:
        # Someone may have cancelled us from another request while we were
        # working. We commit after every step, so that change is visible here.
        db.refresh(execution)
        if execution.status == "cancelled":
            log_event(agent_log, "execution_cancelled", execution_id=execution.id)
            return execution

        if execution.step_count >= max_steps:
            return _finish(
                db,
                execution,
                "max_steps_exceeded",
                error_message=(
                    f"Stopped after {max_steps} steps without reaching a final "
                    "answer. Raise the step limit or simplify the instructions."
                ),
            )

        # --- ask the model what to do next ---
        started = time.monotonic()
        try:
            reply = call_llm(
                system_instruction=build_system_instruction(version),
                contents=build_contents(execution),
                allowed_tool_names=allowed,
            )
        except LLMError as exc:
            _record_step(db, execution, kind="error", error_message=str(exc))
            return _finish(db, execution, "failed", error_message=str(exc))

        duration_ms = int((time.monotonic() - started) * 1000)

        # --- the model gave a final answer ---
        if not reply.tool_name:
            parsed = _extract_json(reply.text)

            if parsed is None:
                _record_step(
                    db,
                    execution,
                    kind="error",
                    llm_text=reply.text,
                    error_message="The model's final answer was not valid JSON.",
                    duration_ms=duration_ms,
                )
                return _finish(
                    db,
                    execution,
                    "failed",
                    error_message="The model did not return valid JSON for its final answer.",
                )

            # Does the answer match the shape the skill promised?
            schema = version.output_schema or {}
            if schema:
                try:
                    jsonschema.validate(instance=parsed, schema=schema)
                except jsonschema.ValidationError as exc:
                    _record_step(
                        db,
                        execution,
                        kind="error",
                        llm_text=reply.text,
                        error_message=f"Output did not match the schema: {exc.message}",
                        duration_ms=duration_ms,
                    )
                    return _finish(
                        db,
                        execution,
                        "failed",
                        error_message=f"The final output did not match the output schema: {exc.message}",
                    )

            _record_step(
                db,
                execution,
                kind="final_output",
                llm_text=reply.text,
                tool_output=parsed,
                duration_ms=duration_ms,
            )
            return _finish(db, execution, "completed", final_output=parsed)

        # --- the model asked for a tool ---
        tool_name = reply.tool_name
        tool_args = reply.tool_args or {}
        tool = tools.get_tool(tool_name)

        # Refusal path: the tool does not exist, or this skill may not use it.
        # We record the refusal and hand it back to the model as a tool result
        # rather than crashing, so it can pick a different approach.
        if tool is None or tool_name not in allowed:
            reason = (
                f"There is no tool called '{tool_name}'."
                if tool is None
                else f"This skill is not permitted to use '{tool_name}'. "
                f"Permitted tools: {', '.join(allowed) or 'none'}."
            )
            log_event(
                agent_log,
                "tool_refused",
                execution_id=execution.id,
                tool=tool_name,
                reason="unknown_tool" if tool is None else "not_allowed",
            )
            _record_step(
                db,
                execution,
                kind="tool_call",
                llm_text=reply.text,
                tool_name=tool_name,
                tool_input=tool_args,
                error_message=reason,
                duration_ms=duration_ms,
            )
            continue

        # Approval path: a write action stops here and waits for a human.
        if tool.is_write or tool_name in needs_approval:
            step = _record_step(
                db,
                execution,
                kind="tool_call",
                llm_text=reply.text,
                tool_name=tool_name,
                tool_input=tool_args,
                duration_ms=duration_ms,
            )

            key = make_idempotency_key(execution.id, step.step_number, tool_name, tool_args)

            approval = models.ApprovalRequest(
                execution_id=execution.id,
                step_number=step.step_number,
                tool_name=tool_name,
                tool_input=tool_args,
                idempotency_key=key,
            )
            db.add(approval)
            try:
                db.commit()
            except IntegrityError:
                # This exact action was already requested. Reuse the existing
                # request instead of creating a duplicate.
                db.rollback()

            execution.status = "awaiting_approval"
            db.commit()
            db.refresh(execution)

            log_event(
                agent_log,
                "approval_requested",
                execution_id=execution.id,
                tool=tool_name,
                step=step.step_number,
            )
            return execution

        # Normal path: a read-only tool runs immediately.
        tool_started = time.monotonic()
        output, error = run_tool_now(db, execution, tool, tool_args)
        tool_ms = int((time.monotonic() - tool_started) * 1000)

        log_event(
            agent_log,
            "tool_call",
            execution_id=execution.id,
            tool=tool_name,
            ok=error is None,
            duration_ms=tool_ms,
        )

        _record_step(
            db,
            execution,
            kind="tool_call",
            llm_text=reply.text,
            tool_name=tool_name,
            tool_input=tool_args,
            tool_output=output,
            error_message=error,
            duration_ms=tool_ms,
        )
        # Loop again - the model now sees the tool result.


# --- approvals ---------------------------------------------------------------


def approve_and_continue(db: Session, approval: models.ApprovalRequest) -> models.Execution:
    """Runs an approved write action exactly once, then resumes the run.

    The duplicate-execution guard is here. We check `executed` and set it in the
    same transaction, so two simultaneous approve clicks cannot both get past it.
    The unique key on the tasks table is the second line of defence.
    """
    execution = approval.execution

    if approval.status == "rejected":
        raise AgentError("This action was already rejected.")

    if approval.executed:
        # Already done. Return the run as-is instead of running the write twice.
        log_event(
            agent_log,
            "approval_replay_ignored",
            execution_id=execution.id,
            approval_id=approval.id,
        )
        return execution

    tool = tools.get_tool(approval.tool_name)
    if tool is None:
        raise AgentError(f"The tool '{approval.tool_name}' no longer exists.")

    # Claim the approval first. If another request already flipped this flag,
    # its commit wins and ours will find executed=True on the next read.
    approval.status = "approved"
    approval.executed = True
    approval.decided_at = utcnow()
    db.commit()

    context = {
        "execution_id": execution.id,
        "idempotency_key": approval.idempotency_key,
    }

    try:
        output = tool.handler(db, approval.tool_input or {}, context)
        error = None
    except tools.ToolError as exc:
        db.rollback()
        output, error = None, str(exc)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        output, error = None, f"The tool failed unexpectedly: {exc}"

    approval.tool_output = output
    db.commit()

    # Write the result onto the step that requested it, so the conversation we
    # rebuild for the model contains the outcome.
    step = (
        db.query(models.ExecutionStep)
        .filter(
            models.ExecutionStep.execution_id == execution.id,
            models.ExecutionStep.step_number == approval.step_number,
        )
        .first()
    )
    if step is not None:
        step.tool_output = output
        step.error_message = error
        db.commit()

    log_event(
        agent_log,
        "approval_granted",
        execution_id=execution.id,
        tool=approval.tool_name,
        ok=error is None,
    )

    return run_execution(db, execution)


def reject_approval(db: Session, approval: models.ApprovalRequest, reason: str = "") -> models.Execution:
    """Records a human 'no' and lets the model continue without that action."""
    execution = approval.execution

    if approval.executed:
        raise AgentError("This action has already run and cannot be rejected.")

    approval.status = "rejected"
    approval.decided_at = utcnow()

    message = "A human rejected this action, so it did not run."
    if reason:
        message += f" Reason: {reason}"

    step = (
        db.query(models.ExecutionStep)
        .filter(
            models.ExecutionStep.execution_id == execution.id,
            models.ExecutionStep.step_number == approval.step_number,
        )
        .first()
    )
    if step is not None:
        step.error_message = message

    db.commit()

    log_event(
        agent_log,
        "approval_rejected",
        execution_id=execution.id,
        tool=approval.tool_name,
    )

    return run_execution(db, execution)


def cancel_execution(db: Session, execution: models.Execution) -> models.Execution:
    """Stops a run. Works while it is paused for approval, and is picked up by
    the loop at the start of its next step if one is in progress."""
    if execution.status in ("completed", "failed", "cancelled"):
        return execution

    return _finish(db, execution, "cancelled", error_message="Cancelled by a user.")

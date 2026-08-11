"""Tests for the agent loop.

These cover the behaviours that must never break: a skill cannot use a tool it
was not granted, a write action cannot happen without approval, an approved
write cannot happen twice, and a run cannot loop forever.
"""

from app import agent, models
from tests.conftest import final_reply, tool_reply

GOOD_ANSWER = '{"answer": "done"}'


# --- tool permissions --------------------------------------------------------


def test_refuses_a_tool_the_skill_was_not_granted(db, fake_llm, make_version, make_execution):
    """The model asks for document_search, but the skill only allows calculator."""
    version = make_version(allowed_tools=["calculator"])
    execution = make_execution(version)

    fake_llm(
        [
            tool_reply("document_search", {"query": "policy"}),
            final_reply(GOOD_ANSWER),
        ]
    )

    result = agent.run_execution(db, execution)

    assert result.status == "completed"

    refusal = result.steps[0]
    assert refusal.tool_name == "document_search"
    assert "not permitted" in refusal.error_message
    # The refusal names what the skill *can* use, so the model can recover.
    assert "calculator" in refusal.error_message
    # Crucially, the refusal did not kill the run.
    assert result.final_output == {"answer": "done"}


def test_refuses_a_tool_that_does_not_exist(db, fake_llm, make_version, make_execution):
    version = make_version(allowed_tools=["calculator"])
    execution = make_execution(version)

    fake_llm([tool_reply("delete_everything", {}), final_reply(GOOD_ANSWER)])

    result = agent.run_execution(db, execution)

    assert result.status == "completed"
    assert "no tool called 'delete_everything'" in result.steps[0].error_message


def test_model_is_only_told_about_permitted_tools(db, fake_llm, make_version, make_execution):
    """The allowlist is enforced twice: the model is not even shown other tools."""
    version = make_version(allowed_tools=["calculator"])
    execution = make_execution(version)

    fake = fake_llm([final_reply(GOOD_ANSWER)])
    agent.run_execution(db, execution)

    assert fake.calls[0]["allowed_tool_names"] == ["calculator"]


# --- approval ----------------------------------------------------------------


def test_write_action_pauses_and_writes_nothing(db, fake_llm, make_version, make_execution):
    version = make_version(
        allowed_tools=["create_task"], approval_required_tools=["create_task"]
    )
    execution = make_execution(version)

    fake_llm([tool_reply("create_task", {"title": "Do the thing"})])

    result = agent.run_execution(db, execution)

    assert result.status == "awaiting_approval"
    # Nothing was written while waiting for a human.
    assert db.query(models.Task).count() == 0

    approval = result.approvals[0]
    assert approval.status == "pending"
    assert approval.executed is False
    assert approval.tool_input == {"title": "Do the thing"}


def test_approval_runs_the_write_exactly_once(db, fake_llm, make_version, make_execution):
    version = make_version(
        allowed_tools=["create_task"], approval_required_tools=["create_task"]
    )
    execution = make_execution(version)

    fake_llm([tool_reply("create_task", {"title": "Do the thing"}), final_reply(GOOD_ANSWER)])
    paused = agent.run_execution(db, execution)

    approval = paused.approvals[0]
    result = agent.approve_and_continue(db, approval)

    assert result.status == "completed"
    assert db.query(models.Task).count() == 1
    assert db.query(models.Task).first().title == "Do the thing"


def test_approving_twice_does_not_create_two_tasks(db, fake_llm, make_version, make_execution):
    """The duplicate-write guard. A double click must not write twice."""
    version = make_version(
        allowed_tools=["create_task"], approval_required_tools=["create_task"]
    )
    execution = make_execution(version)

    fake_llm([tool_reply("create_task", {"title": "Only once"}), final_reply(GOOD_ANSWER)])
    paused = agent.run_execution(db, execution)
    approval = paused.approvals[0]

    agent.approve_and_continue(db, approval)
    assert db.query(models.Task).count() == 1

    # Second approval of the same request.
    agent.approve_and_continue(db, approval)
    assert db.query(models.Task).count() == 1


def test_rejecting_a_write_creates_nothing_and_run_continues(
    db, fake_llm, make_version, make_execution
):
    version = make_version(
        allowed_tools=["create_task"], approval_required_tools=["create_task"]
    )
    execution = make_execution(version)

    fake_llm([tool_reply("create_task", {"title": "Should not exist"}), final_reply(GOOD_ANSWER)])
    paused = agent.run_execution(db, execution)
    approval = paused.approvals[0]

    result = agent.reject_approval(db, approval, "Not appropriate.")

    assert result.status == "completed"
    assert db.query(models.Task).count() == 0
    assert approval.status == "rejected"
    assert approval.executed is False


def test_write_tool_needs_approval_even_if_not_listed(db, fake_llm, make_version, make_execution):
    """Platform policy beats skill configuration: a write always pauses."""
    version = make_version(allowed_tools=["create_task"], approval_required_tools=[])
    execution = make_execution(version)

    fake_llm([tool_reply("create_task", {"title": "Sneaky"})])
    result = agent.run_execution(db, execution)

    assert result.status == "awaiting_approval"
    assert db.query(models.Task).count() == 0


# --- limits and failures -----------------------------------------------------


def test_stops_at_the_step_limit(db, fake_llm, make_version, make_execution):
    version = make_version(allowed_tools=["calculator"], max_steps=3)
    execution = make_execution(version)

    # The model never finishes - it just keeps calling the calculator.
    fake_llm([tool_reply("calculator", {"expression": "1+1"}) for _ in range(10)])

    result = agent.run_execution(db, execution)

    assert result.status == "max_steps_exceeded"
    assert result.step_count == 3
    assert "3 steps" in result.error_message


def test_a_failing_tool_is_reported_back_instead_of_crashing(
    db, fake_llm, make_version, make_execution
):
    version = make_version(allowed_tools=["calculator"])
    execution = make_execution(version)

    # Division by zero is a tool error, not a crash.
    fake_llm(
        [
            tool_reply("calculator", {"expression": "1/0"}),
            final_reply(GOOD_ANSWER),
        ]
    )

    result = agent.run_execution(db, execution)

    assert result.status == "completed"
    assert "Division by zero" in result.steps[0].error_message
    # The error was handed back to the model as a tool result.
    assert result.steps[0].tool_output is None


def test_cancelling_stops_the_run(db, fake_llm, make_version, make_execution):
    version = make_version(
        allowed_tools=["create_task"], approval_required_tools=["create_task"]
    )
    execution = make_execution(version)

    fake_llm([tool_reply("create_task", {"title": "Pending"})])
    paused = agent.run_execution(db, execution)

    result = agent.cancel_execution(db, paused)

    assert result.status == "cancelled"
    assert db.query(models.Task).count() == 0


# --- output handling ---------------------------------------------------------


def test_a_malformed_answer_is_sent_back_for_correction(
    db, fake_llm, make_version, make_execution
):
    version = make_version()
    execution = make_execution(version)

    fake = fake_llm(
        [
            final_reply("Sorry, I could not find that information."),  # not JSON
            final_reply(GOOD_ANSWER),
        ]
    )

    result = agent.run_execution(db, execution)

    assert result.status == "completed"
    assert result.final_output == {"answer": "done"}

    rejected = [s for s in result.steps if s.kind == "invalid_output"]
    assert len(rejected) == 1
    # The correction was actually shown to the model on the retry.
    assert "valid JSON" in fake.calls[1]["contents"][-1]["parts"][0]["text"]


def test_gives_up_after_repeated_malformed_answers(db, fake_llm, make_version, make_execution):
    version = make_version()
    execution = make_execution(version)

    fake_llm([final_reply("still not json"), final_reply("nope")])

    result = agent.run_execution(db, execution)

    assert result.status == "failed"
    assert "output schema" in result.error_message


def test_output_must_match_the_declared_schema(db, fake_llm, make_version, make_execution):
    version = make_version()
    execution = make_execution(version)

    # Valid JSON, but missing the required "answer" field.
    fake_llm([final_reply('{"result": "wrong shape"}'), final_reply('{"result": "still wrong"}')])

    result = agent.run_execution(db, execution)

    assert result.status == "failed"


def test_json_wrapped_in_a_code_fence_is_accepted(db, fake_llm, make_version, make_execution):
    version = make_version()
    execution = make_execution(version)

    fake_llm([final_reply('```json\n{"answer": "fenced"}\n```')])

    result = agent.run_execution(db, execution)

    assert result.status == "completed"
    assert result.final_output == {"answer": "fenced"}


# --- idempotency key ---------------------------------------------------------


def test_idempotency_key_is_stable_for_the_same_action():
    a = agent.make_idempotency_key(1, 2, "create_task", {"title": "x", "assignee": "y"})
    b = agent.make_idempotency_key(1, 2, "create_task", {"assignee": "y", "title": "x"})
    # Key order in the arguments must not change the fingerprint.
    assert a == b


def test_idempotency_key_differs_for_different_actions():
    a = agent.make_idempotency_key(1, 2, "create_task", {"title": "x"})
    b = agent.make_idempotency_key(1, 2, "create_task", {"title": "different"})
    c = agent.make_idempotency_key(1, 3, "create_task", {"title": "x"})
    assert a != b
    assert a != c

"""
Database tables.

The shape of the whole app is in these five core tables:

    Skill            one named capability, e.g. "Expense Triager"
    SkillVersion     an immutable snapshot of that skill's definition (v1, v2, ...)
    Execution        one run of one SkillVersion
    ExecutionStep    one thing that happened during a run (llm call / tool call / final answer)
    ApprovalRequest  a pause point: a write action waiting for a human "yes"

Plus three small tables that exist only as data for the tools to read and write:

    Document         what the document_search tool searches
    Record           what the record_lookup tool reads
    Task             what the create_task tool writes (the one write action)

Why versions are separate from skills:
    Editing a skill must not silently change runs that already happened. So a
    Skill is just a name, and all the real content lives in SkillVersion rows.
    Once a version is published it is never edited again - you create a new one.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db import Base

# --- Allowed values for our status columns -----------------------------------
# Plain strings rather than SQL enums: easier to read, and easier to change
# without a migration. The API layer validates against these lists.

VERSION_STATUSES = ["draft", "published", "archived"]

EXECUTION_STATUSES = [
    "running",             # agent loop is working
    "awaiting_approval",   # paused, a human must approve a write action
    "completed",           # finished and produced a final output
    "failed",              # stopped because of an error
    "cancelled",           # a human stopped it
    "max_steps_exceeded",  # hit the step limit without finishing
]

STEP_KINDS = [
    "llm_call",
    "tool_call",
    "approval",
    "final_output",
    "invalid_output",  # a final answer we rejected and asked the model to redo
    "error",
]

APPROVAL_STATUSES = ["pending", "approved", "rejected"]


def utcnow() -> datetime:
    """Timezone-aware 'now'. Used as the default for every timestamp column."""
    return datetime.now(timezone.utc)


# --- Core tables -------------------------------------------------------------


class Skill(Base):
    """A named capability. Holds no logic itself - see SkillVersion."""

    __tablename__ = "skills"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, unique=True)
    purpose = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    versions = relationship(
        "SkillVersion",
        back_populates="skill",
        cascade="all, delete-orphan",
        order_by="SkillVersion.version_number",
    )


class SkillVersion(Base):
    """One snapshot of a skill's definition.

    A version starts as a 'draft' (editable). Publishing it flips the status to
    'published' and freezes it - from then on the only way to change anything is
    to create a new draft version. That is what makes "compare versions" and
    "rerun an old version" meaningful.
    """

    __tablename__ = "skill_versions"
    # Two versions of the same skill can never share a version number.
    __table_args__ = (UniqueConstraint("skill_id", "version_number", name="uq_skill_version"),)

    id = Column(Integer, primary_key=True)
    skill_id = Column(Integer, ForeignKey("skills.id"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="draft")

    # --- the actual skill definition ---
    instructions = Column(Text, nullable=False, default="")
    # JSON Schema describing what input this skill accepts.
    input_schema = Column(JSON, nullable=False, default=dict)
    # JSON Schema describing the shape of the final answer.
    output_schema = Column(JSON, nullable=False, default=dict)
    # List of {"input": {...}, "output": {...}} to show the model what good looks like.
    examples = Column(JSON, nullable=False, default=list)
    # List of tool names this skill may use. Anything not in here is refused.
    allowed_tools = Column(JSON, nullable=False, default=list)
    # Subset of allowed_tools that must be approved by a human before running.
    approval_required_tools = Column(JSON, nullable=False, default=list)
    # How many loop iterations this skill gets before we stop it.
    max_steps = Column(Integer, nullable=False, default=8)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=True)

    skill = relationship("Skill", back_populates="versions")
    executions = relationship("Execution", back_populates="skill_version")

    @property
    def is_editable(self) -> bool:
        """Only drafts can be edited. Enforced in the API layer too."""
        return self.status == "draft"


class Execution(Base):
    """One run of one skill version."""

    __tablename__ = "executions"

    id = Column(Integer, primary_key=True)
    skill_version_id = Column(Integer, ForeignKey("skill_versions.id"), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="running")

    input_data = Column(JSON, nullable=False, default=dict)
    final_output = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    # How many loop iterations we have used so far, checked against max_steps.
    step_count = Column(Integer, nullable=False, default=0)

    # Set when this run was started by "rerun" on an older execution, so the
    # history view can show that link.
    rerun_of_execution_id = Column(Integer, ForeignKey("executions.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    skill_version = relationship("SkillVersion", back_populates="executions")
    steps = relationship(
        "ExecutionStep",
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="ExecutionStep.step_number",
    )
    approvals = relationship(
        "ApprovalRequest",
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="ApprovalRequest.id",
    )


class ExecutionStep(Base):
    """One recorded thing that happened during a run.

    Every LLM call, every tool call, every error and the final answer all become
    a row here. This is what makes a run auditable and replayable after the fact.
    """

    __tablename__ = "execution_steps"

    id = Column(Integer, primary_key=True)
    execution_id = Column(Integer, ForeignKey("executions.id"), nullable=False, index=True)
    step_number = Column(Integer, nullable=False)
    kind = Column(String(20), nullable=False)

    # The model's own words: its plan, or its reasoning before a tool call.
    llm_text = Column(Text, nullable=True)

    # Filled in only when kind == "tool_call".
    tool_name = Column(String(80), nullable=True)
    tool_input = Column(JSON, nullable=True)
    tool_output = Column(JSON, nullable=True)

    # The provider's reply for this step, stored exactly as it arrived.
    #
    # We rebuild the conversation from these rows on every turn, and modern
    # models attach data to their replies that must come back unchanged - Gemini
    # 3 signs its reasoning with a `thoughtSignature` and rejects the request if
    # the signature does not return with the tool call it belongs to. It also
    # omits that signature sometimes, so reconstructing the turn from our own
    # fields is guesswork. Replaying the original content verbatim is both
    # simpler and always correct.
    model_content = Column(JSON, nullable=True)

    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    execution = relationship("Execution", back_populates="steps")


class ApprovalRequest(Base):
    """A write action that is paused, waiting for a human decision.

    The duplicate-write guard lives here. Two things stop the same approved
    action running twice:

      1. `idempotency_key` has a UNIQUE constraint, so the database itself
         refuses a second identical request.
      2. `executed` is a flag we check and set inside one transaction, so even
         two simultaneous "Approve" clicks can only result in one run.
    """

    __tablename__ = "approval_requests"

    id = Column(Integer, primary_key=True)
    execution_id = Column(Integer, ForeignKey("executions.id"), nullable=False, index=True)
    step_number = Column(Integer, nullable=False)

    tool_name = Column(String(80), nullable=False)
    tool_input = Column(JSON, nullable=False, default=dict)

    status = Column(String(20), nullable=False, default="pending")

    # Unique per (execution, step, tool, arguments). See the class docstring.
    idempotency_key = Column(String(200), nullable=False, unique=True)

    # True once the approved action has actually run. Never runs twice.
    executed = Column(Boolean, nullable=False, default=False)
    tool_output = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=True)

    execution = relationship("Execution", back_populates="approvals")


# --- Data the tools operate on ----------------------------------------------


class Document(Base):
    """Searched by the `document_search` tool."""

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)


class Record(Base):
    """Looked up by the `record_lookup` tool. A stand-in for a real business
    database (customers, orders, and so on)."""

    __tablename__ = "records"

    id = Column(Integer, primary_key=True)
    record_type = Column(String(50), nullable=False, index=True)
    external_id = Column(String(50), nullable=False, index=True)
    data = Column(JSON, nullable=False, default=dict)


class Task(Base):
    """Created by the `create_task` tool - the only write action in the system.

    `idempotency_key` is UNIQUE here as well. Even if every layer of application
    logic failed, the database still could not store the same task twice.
    """

    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="")
    assignee = Column(String(120), nullable=False, default="unassigned")

    created_by_execution_id = Column(Integer, ForeignKey("executions.id"), nullable=True)
    idempotency_key = Column(String(200), nullable=True, unique=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

"""Shared test setup.

Two things matter here:

  * The database is a throwaway SQLite file, rebuilt for every test, so tests
    never see each other's data and never touch your real one.

  * The language model is replaced with a scripted fake. Real model calls would
    be slow, non-deterministic, and would burn the free tier's daily quota, so
    every test that exercises the agent says exactly what the model "replies".
    That lets us test the parts that must never break - refusals, approvals,
    duplicate prevention, step limits - precisely and repeatably.
"""

import os
import tempfile

import pytest

# Must be set before anything imports app.db, which reads it at import time.
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(tempfile.mkdtemp(), 'test.db')}"
os.environ.setdefault("GEMINI_API_KEY", "test-key-never-used")

from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402
from app.db import Base, SessionLocal, engine, get_db  # noqa: E402
from app.llm import LLMResponse  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def db():
    """A clean database for one test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    """API client sharing the test's database session.

    Deliberately not used as a context manager: that would run the app's
    startup hook, which seeds demo data and would make assertions about
    "how many skills exist" depend on the seed.
    """
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- the scripted model ------------------------------------------------------


def tool_reply(name: str, args: dict, text: str = "") -> LLMResponse:
    """A reply in which the model asks to use a tool."""
    return LLMResponse(
        text=text,
        tool_name=name,
        tool_args=args,
        raw={},
        content={"role": "model", "parts": [{"functionCall": {"name": name, "args": args}}]},
    )


def final_reply(text: str) -> LLMResponse:
    """A reply in which the model gives its final answer."""
    return LLMResponse(
        text=text,
        tool_name=None,
        tool_args={},
        raw={},
        content={"role": "model", "parts": [{"text": text}]},
    )


class FakeLLM:
    """Returns pre-written replies in order and records what it was asked."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, system_instruction, contents, allowed_tool_names):
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "contents": contents,
                "allowed_tool_names": list(allowed_tool_names),
            }
        )
        if not self.replies:
            raise AssertionError(
                "The agent asked the model for more replies than the test provided. "
                f"It has made {len(self.calls)} calls."
            )
        return self.replies.pop(0)


@pytest.fixture()
def fake_llm(monkeypatch):
    """Installs a scripted model. Usage: `fake_llm([tool_reply(...), final_reply(...)])`."""

    def install(replies):
        fake = FakeLLM(replies)
        monkeypatch.setattr("app.agent.call_llm", fake)
        return fake

    return install


# --- building skills to test against -----------------------------------------

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


@pytest.fixture()
def make_version(db):
    """Creates a published skill version with whatever settings a test needs."""
    counter = {"n": 0}

    def build(
        allowed_tools=None,
        approval_required_tools=None,
        max_steps=8,
        instructions="Answer the question using the tools you have been given.",
        output_schema=None,
    ):
        counter["n"] += 1
        skill = models.Skill(name=f"Test Skill {counter['n']}", purpose="testing")
        db.add(skill)
        db.flush()

        version = models.SkillVersion(
            skill_id=skill.id,
            version_number=1,
            status="published",
            published_at=models.utcnow(),
            instructions=instructions,
            input_schema={"type": "object", "properties": {"question": {"type": "string"}}},
            output_schema=OUTPUT_SCHEMA if output_schema is None else output_schema,
            examples=[],
            allowed_tools=allowed_tools or [],
            approval_required_tools=approval_required_tools or [],
            max_steps=max_steps,
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        return version

    return build


@pytest.fixture()
def make_execution(db):
    def build(version, input_data=None):
        execution = models.Execution(
            skill_version_id=version.id,
            status="running",
            input_data=input_data or {"question": "test"},
        )
        db.add(execution)
        db.commit()
        db.refresh(execution)
        return execution

    return build

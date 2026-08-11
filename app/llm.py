"""
The LLM provider.

This is the only file in the project that knows about Google Gemini. Everything
else talks to `call_llm(...)` and gets back a small, provider-neutral object.
Swapping to another provider means rewriting this one file.

We call Gemini's REST API directly with httpx rather than using a vendor SDK.
That keeps the dependency list small and makes the request and response shapes
visible, which is easier to debug and easier to explain.
"""

import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import GEMINI_API_KEY, GEMINI_MODEL
from app.logging_setup import agent_log, log_event
from app.tools import TOOLS

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

# How many times to retry a call that failed for a temporary reason
# (rate limit, provider hiccup). Permanent errors are not retried.
MAX_ATTEMPTS = 3
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


class LLMError(Exception):
    """Raised when we could not get a usable answer from the model."""


@dataclass
class LLMResponse:
    """What came back from the model.

    Exactly one of these is meaningful at a time:
      - tool_name / tool_args set  -> the model wants to call a tool
      - tool_name is None          -> the model gave its final text answer
    """

    text: str
    tool_name: str | None
    tool_args: dict
    raw: dict


def build_tool_declarations(allowed_tool_names: list[str]) -> list[dict]:
    """Describes the permitted tools in the format Gemini expects.

    Only tools in `allowed_tool_names` are described. This is the first half of
    the permission model: the model is never even told that other tools exist.
    The second half is the hard check in agent.py, which refuses a call to any
    tool outside the list even if the model invents one.
    """
    declarations = []
    for name in allowed_tool_names:
        tool = TOOLS.get(name)
        if tool is None:
            continue
        declarations.append(
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
        )
    return declarations


def _parse_response(payload: dict) -> LLMResponse:
    """Pulls the interesting bits out of Gemini's response envelope."""
    candidates = payload.get("candidates") or []
    if not candidates:
        # Usually means the prompt was blocked by a safety filter.
        feedback = payload.get("promptFeedback", {})
        raise LLMError(f"The model returned no answer. Provider feedback: {feedback}")

    parts = candidates[0].get("content", {}).get("parts") or []

    text_pieces: list[str] = []
    tool_name: str | None = None
    tool_args: dict = {}

    for part in parts:
        if "text" in part:
            text_pieces.append(part["text"])
        elif "functionCall" in part:
            call = part["functionCall"]
            tool_name = call.get("name")
            tool_args = call.get("args") or {}

    return LLMResponse(
        text="\n".join(text_pieces).strip(),
        tool_name=tool_name,
        tool_args=tool_args,
        raw=payload,
    )


def call_llm(
    system_instruction: str,
    contents: list[dict],
    allowed_tool_names: list[str],
) -> LLMResponse:
    """Sends one turn to the model and returns its reply.

    `contents` is the running conversation: the original input, every tool call
    the model made, and every tool result we fed back.
    """
    if not GEMINI_API_KEY:
        raise LLMError(
            "No GEMINI_API_KEY is configured, so the agent cannot run. "
            "Set it in your .env file or in the deployment's secrets."
        )

    body: dict[str, Any] = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.2},
    }

    declarations = build_tool_declarations(allowed_tool_names)
    if declarations:
        body["tools"] = [{"function_declarations": declarations}]

    url = f"{API_ROOT}/{GEMINI_MODEL}:generateContent"
    last_error: str = ""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            response = httpx.post(
                url,
                params={"key": GEMINI_API_KEY},
                json=body,
                timeout=60.0,
            )
        except httpx.RequestError as exc:
            # Network-level problem: worth retrying.
            last_error = f"Could not reach the model provider: {exc}"
            log_event(
                agent_log,
                "llm_call_failed",
                attempt=attempt,
                reason="network",
                error=str(exc),
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(attempt)  # 1s, then 2s
                continue
            raise LLMError(last_error) from exc

        elapsed_ms = int((time.monotonic() - started) * 1000)

        if response.status_code in RETRY_STATUS_CODES:
            last_error = f"Provider returned {response.status_code}: {response.text[:300]}"
            log_event(
                agent_log,
                "llm_call_retryable_error",
                attempt=attempt,
                status_code=response.status_code,
                duration_ms=elapsed_ms,
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(attempt)
                continue
            raise LLMError(last_error)

        if response.status_code != 200:
            # A permanent error (bad key, malformed request). Retrying will not
            # help, so fail immediately with a message a human can act on.
            log_event(
                agent_log,
                "llm_call_failed",
                attempt=attempt,
                status_code=response.status_code,
                duration_ms=elapsed_ms,
            )
            raise LLMError(
                f"Model provider rejected the request ({response.status_code}): "
                f"{response.text[:300]}"
            )

        log_event(
            agent_log,
            "llm_call_ok",
            attempt=attempt,
            duration_ms=elapsed_ms,
            model=GEMINI_MODEL,
        )
        return _parse_response(response.json())

    raise LLMError(last_error or "The model could not be reached.")

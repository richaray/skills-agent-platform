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

from app.config import GEMINI_API_KEY, model_chain
from app.logging_setup import agent_log, log_event
from app.tools import TOOLS

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

# How many times to retry a call that failed for a temporary reason
# (rate limit, provider hiccup). Permanent errors are not retried.
MAX_ATTEMPTS = 3
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

# Wait between retries of a temporary server-side failure. A 429 is handled
# differently: the free tier's quota is per day, so waiting is pointless and we
# switch to the next model in the chain instead.
SERVER_ERROR_BACKOFF_SECONDS = 2


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
    # The model's reply exactly as the provider sent it. Stored and replayed
    # unchanged on the next turn - see models.py for why that matters.
    content: dict | None = None


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

    content = candidates[0].get("content", {}) or {}
    parts = content.get("parts") or []

    text_pieces: list[str] = []
    tool_name: str | None = None
    tool_args: dict = {}
    call_count = 0

    for part in parts:
        if "text" in part:
            text_pieces.append(part["text"])
        elif "functionCall" in part:
            call_count += 1
            # We act on one tool call per turn. If the model ever asks for
            # several at once we take the first and log it, rather than
            # silently dropping the others.
            if tool_name is None:
                call = part["functionCall"]
                tool_name = call.get("name")
                tool_args = call.get("args") or {}

    # Gemini sometimes asks for several tools at once. The API then requires one
    # functionResponse for every functionCall in that turn, and if the counts do
    # not match it replies with an empty completion and no error - which is very
    # hard to debug.
    #
    # We run one tool per step, so we normalise the turn down to its first call
    # before storing it. The conversation we replay is then self-consistent:
    # one call, one response. The dropped calls are not lost - the model simply
    # asks for them again on the next turn if it still needs them.
    if call_count > 1:
        first_call_part = next(part for part in parts if "functionCall" in part)
        content = {"role": "model", "parts": [first_call_part]}
        log_event(
            agent_log,
            "trimmed_parallel_tool_calls",
            requested=call_count,
            kept=tool_name,
        )

    # Responses are matched to calls by tool name, which is sufficient because
    # there is only ever one call per turn.
    content.setdefault("role", "model")

    return LLMResponse(
        text="\n".join(text_pieces).strip(),
        tool_name=tool_name,
        tool_args=tool_args,
        raw=payload,
        content=content,
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

    chain = model_chain()
    quota_exhausted: list[str] = []
    last_error = ""

    for model in chain:
        url = f"{API_ROOT}/{model}:generateContent"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.monotonic()

            try:
                response = httpx.post(
                    url, params={"key": GEMINI_API_KEY}, json=body, timeout=60.0
                )
            except httpx.RequestError as exc:
                # A network problem is temporary and not the model's fault, so
                # we retry the same model rather than moving on.
                last_error = f"Could not reach the AI provider: {exc}"
                log_event(agent_log, "llm_network_error", model=model, attempt=attempt)
                if attempt < MAX_ATTEMPTS:
                    time.sleep(SERVER_ERROR_BACKOFF_SECONDS * attempt)
                    continue
                break

            elapsed_ms = int((time.monotonic() - started) * 1000)

            if response.status_code == 200:
                log_event(
                    agent_log,
                    "llm_call_ok",
                    model=model,
                    attempt=attempt,
                    duration_ms=elapsed_ms,
                    fell_back=model != chain[0],
                )
                return _parse_response(response.json())

            # Daily quota gone for this model. Waiting will not help - it resets
            # tomorrow - so move straight to the next model in the chain.
            if response.status_code == 429:
                quota_exhausted.append(model)
                last_error = "The AI provider's free quota is exhausted for this model."
                log_event(
                    agent_log,
                    "llm_quota_exhausted",
                    model=model,
                    duration_ms=elapsed_ms,
                    remaining_models=len(chain) - len(quota_exhausted),
                )
                break

            # The model name is gone. Nothing to retry; try the next one.
            if response.status_code == 404:
                last_error = f"The model '{model}' is no longer available."
                log_event(agent_log, "llm_model_unavailable", model=model)
                break

            # A temporary server-side problem: retry the same model.
            if response.status_code in RETRY_STATUS_CODES:
                last_error = f"The AI provider returned {response.status_code}."
                log_event(
                    agent_log,
                    "llm_server_error",
                    model=model,
                    status_code=response.status_code,
                    attempt=attempt,
                )
                if attempt < MAX_ATTEMPTS:
                    time.sleep(SERVER_ERROR_BACKOFF_SECONDS * attempt)
                    continue
                break

            # Anything else is our fault (bad key, malformed request). Retrying
            # or switching models will not help, so stop with a clear message.
            log_event(
                agent_log,
                "llm_request_rejected",
                model=model,
                status_code=response.status_code,
            )
            raise LLMError(
                f"The AI provider rejected the request ({response.status_code}): "
                f"{response.text[:300]}"
            )

    if len(quota_exhausted) == len(chain):
        raise LLMError(
            "Every configured AI model has used up its free daily quota. "
            "Google's free tier allows only a limited number of requests per model "
            "per day, and it resets at midnight Pacific time. Everything else in the "
            "app still works - only running a skill is unavailable."
        )

    raise LLMError(last_error or "The AI provider could not be reached.")

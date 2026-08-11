"""
Structured logging.

Every log line is a single JSON object. That matters because the interesting
logs here are about an AI workflow - "which tool did step 3 call, and how long
did it take" - and those are much easier to read and filter as structured
fields than as sentences.

Two loggers:
    app    - ordinary web application events (requests, validation failures)
    agent  - the AI workflow (llm calls, tool calls, approvals, refusals)
"""

import json
import logging
import sys
from datetime import datetime, timezone

from app.config import LOG_LEVEL


class JsonFormatter(logging.Formatter):
    """Renders each log record as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Anything passed as logger.info("msg", extra={"context": {...}})
        # gets merged into the JSON line.
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            entry.update(context)

        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


def setup_logging() -> None:
    """Called once on startup. Sends JSON logs to stdout, which is where
    Hugging Face Spaces (and Docker generally) collects them from."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(LOG_LEVEL)

    # uvicorn ships its own noisy handlers; make them use ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True


app_log = logging.getLogger("app")
agent_log = logging.getLogger("agent")


def log_event(logger: logging.Logger, message: str, **fields) -> None:
    """Small helper so call sites stay readable:

        log_event(agent_log, "tool_call", execution_id=3, tool="calculator")
    """
    logger.info(message, extra={"context": fields})

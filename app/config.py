"""
All configuration in one place, read from environment variables.

Kept deliberately simple: plain os.environ reads with sensible defaults, so you
can see exactly where every setting comes from. Locally, values are loaded from
a .env file; in production (Hugging Face Spaces) they come from real env vars.
"""

import os

from dotenv import load_dotenv

# Loads .env into os.environ when running locally.
# In production there is no .env file and this call simply does nothing.
load_dotenv()

# --- LLM ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# --- Database ---
# Defaults to a local SQLite file so the app runs with zero setup.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./local.db")

# --- App ---
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Safety rail: no skill is allowed to loop more times than this, no matter what
# the skill author sets. Stops a runaway agent from burning the API quota.
HARD_MAX_STEPS = 15


def llm_is_configured() -> bool:
    """True if we have an API key. The UI uses this to show a clear warning
    instead of failing with a confusing error halfway through a run."""
    return bool(GEMINI_API_KEY)

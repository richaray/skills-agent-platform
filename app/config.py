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
# The model we try first.
#
# A flash-lite model is the default deliberately. Google's free tier caps
# requests PER DAY PER MODEL - gemini-3.5-flash allows only 20, which one person
# testing the app would exhaust in about three runs. The lite models are faster
# and have far more generous limits, and this workload (following explicit
# instructions and calling tools) does not need a frontier model.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")

# Tried in order when the primary model is unavailable - its daily quota is
# exhausted, or Google has retired the name. Because the quota is counted per
# model, this chain multiplies how much the app can serve in a day before it
# has to tell users to come back tomorrow.
GEMINI_FALLBACK_MODELS = [
    name.strip()
    for name in os.environ.get(
        "GEMINI_FALLBACK_MODELS",
        "gemini-flash-lite-latest,gemini-3.6-flash,gemini-3-flash-preview,gemini-flash-latest",
    ).split(",")
    if name.strip()
]


def model_chain() -> list[str]:
    """Every model to try, in order, without duplicates."""
    chain = [GEMINI_MODEL]
    for name in GEMINI_FALLBACK_MODELS:
        if name not in chain:
            chain.append(name)
    return chain

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

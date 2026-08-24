"""Configuration and Vertex AI client factory."""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from google import genai

load_dotenv()


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer; received {value!r}") from exc


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name, str(default))
    try:
        parsed = float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number; received {value!r}") from exc
    if not 0 <= parsed <= 1:
        raise RuntimeError(f"{name} must be between 0 and 1; received {parsed}")
    return parsed


PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_UPLOAD_MB = _get_int("MAX_UPLOAD_MB", 15)
MAX_IMAGE_EDGE = _get_int("MAX_IMAGE_EDGE", 2400)
CONFIDENCE_THRESHOLD = _get_float("CONFIDENCE_THRESHOLD", 0.80)


@lru_cache(maxsize=1)
def get_client() -> genai.Client:
    """Return a cached google-genai client configured for Vertex AI."""
    if not PROJECT_ID:
        raise RuntimeError(
            "No GCP project configured. Set GOOGLE_CLOUD_PROJECT (or PROJECT_ID) "
            "in .env. See .env.example."
        )
    return genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)

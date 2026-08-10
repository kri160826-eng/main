"""Central configuration loaded from environment variables.

Nothing that identifies a customer's data (project id, dataset, bucket) is
hardcoded. Those always come from the UI. This module only holds runtime
configuration and optional convenience defaults for pre-filling the form.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Load a local .env if present. On Cloud Run the environment is injected
# directly and there is simply no .env file to load, which is fine.
load_dotenv()


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of runtime settings."""

    # Gemini / LLM.
    # Two backends:
    #  * Vertex AI (GOOGLE_GENAI_USE_VERTEXAI=true) - auth via ADC / the
    #    attached service account; no API key needed. Uses GOOGLE_CLOUD_PROJECT
    #    and GOOGLE_CLOUD_LOCATION.
    #  * AI Studio (GEMINI_API_KEY / GOOGLE_API_KEY) - auth via an API key.
    gemini_model: str = field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    )
    use_vertex: bool = field(default_factory=lambda: _get_bool("GOOGLE_GENAI_USE_VERTEXAI"))
    vertex_project: str | None = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("VERTEX_PROJECT")
        or None
    )
    vertex_location: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION")
        or os.getenv("VERTEX_LOCATION")
        or "us-central1"
    )
    gemini_api_key: str | None = field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None
    )

    # GCP
    bq_location: str = field(default_factory=lambda: os.getenv("BQ_LOCATION", "US"))
    google_credentials: str | None = field(
        default_factory=lambda: os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or None
    )

    # UI convenience defaults (never required)
    default_project: str = field(default_factory=lambda: os.getenv("DEFAULT_GCP_PROJECT", ""))
    default_silver: str = field(default_factory=lambda: os.getenv("DEFAULT_SILVER_DATASET", ""))
    default_gold: str = field(default_factory=lambda: os.getenv("DEFAULT_GOLD_DATASET", ""))
    default_bucket: str = field(default_factory=lambda: os.getenv("DEFAULT_GCS_BUCKET", ""))

    # Analysis limits
    sample_row_limit: int = field(default_factory=lambda: _get_int("SAMPLE_ROW_LIMIT", 20))
    max_tables: int = field(default_factory=lambda: _get_int("MAX_TABLES", 100))

    # Logging
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    @property
    def llm_enabled(self) -> bool:
        return self.use_vertex or bool(self.gemini_api_key)

    @property
    def engine_label(self) -> str:
        if self.use_vertex:
            loc = self.vertex_location
            return f"Vertex AI Gemini ({self.gemini_model} @ {loc})"
        if self.gemini_api_key:
            return f"Gemini AI Studio ({self.gemini_model})"
        return "Heuristic (no LLM configured)"


def get_settings() -> Settings:
    """Return a fresh Settings snapshot (cheap; reads env each call)."""
    return Settings()

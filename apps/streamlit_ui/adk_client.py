"""
Thin client for the ADK agent service's REST API.

The agent service is the *unmodified* dm_agent wrapped by
google.adk.cli.fast_api.get_fast_api_app (see agent_service/main.py).
This module just knows how to:
  1. create / fetch a session
  2. send a user message and stream back events over SSE
  3. authenticate the call with a Cloud Run ID token when the two
     services are deployed separately and the agent service is
     private (recommended).

No ADK code runs in this process -- this is a plain HTTP client.
"""

from __future__ import annotations

import json
import os
from typing import Any, Generator, Optional

import requests

AGENT_SERVICE_URL = os.environ.get(
    "AGENT_SERVICE_URL", "http://localhost:8080"
).rstrip("/")
APP_NAME = os.environ.get("ADK_APP_NAME", "dm_agent")

# How long we let a single non-streaming HTTP call run before giving up.
# The agent's workflow can be long (BigQuery DDL + GCS writes), so this
# is generous. The SSE stream has no single read timeout other than the
# per-chunk read below.
REQUEST_TIMEOUT_SECS = int(os.environ.get("AGENT_REQUEST_TIMEOUT", "900"))


class AgentServiceError(RuntimeError):
    pass


def _id_token_for(audience: str) -> Optional[str]:
    """Fetch a Google-signed OIDC ID token for `audience`.

    Works automatically when this process has Application Default
    Credentials available — e.g. when running on Cloud Run with a
    service account attached, or locally after `gcloud auth
    application-default login`. Returns None (no auth header) if a
    token can't be obtained, so this also degrades gracefully for
    local development against an open/unauthenticated agent service.
    """
    if os.environ.get("DISABLE_ID_TOKEN_AUTH", "").lower() == "true":
        return None
    try:
        import google.auth.transport.requests
        import google.oauth2.id_token

        auth_req = google.auth.transport.requests.Request()
        return google.oauth2.id_token.fetch_id_token(auth_req, audience)
    except Exception:
        # Most common cause: no ADC available (pure local dev). Caller
        # falls back to unauthenticated requests in that case.
        return None


def _headers() -> dict:
    headers = {"Content-Type": "application/json"}
    token = _id_token_for(AGENT_SERVICE_URL)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def get_session(user_id: str, session_id: str) -> Optional[dict]:
    url = f"{AGENT_SERVICE_URL}/apps/{APP_NAME}/users/{user_id}/sessions/{session_id}"
    resp = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT_SECS)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def create_session(
    user_id: str, session_id: str, state: Optional[dict] = None
) -> dict:
    url = f"{AGENT_SERVICE_URL}/apps/{APP_NAME}/users/{user_id}/sessions"
    payload = {"session_id": session_id, "state": state or {}}
    resp = requests.post(
        url, headers=_headers(), json=payload, timeout=REQUEST_TIMEOUT_SECS
    )
    resp.raise_for_status()
    return resp.json()


def get_or_create_session(user_id: str, session_id: str) -> dict:
    existing = get_session(user_id, session_id)
    if existing:
        return existing
    return create_session(user_id, session_id)


def delete_session(user_id: str, session_id: str) -> None:
    url = f"{AGENT_SERVICE_URL}/apps/{APP_NAME}/users/{user_id}/sessions/{session_id}"
    resp = requests.delete(url, headers=_headers(), timeout=REQUEST_TIMEOUT_SECS)
    resp.raise_for_status()


def stream_message(
    user_id: str, session_id: str, text: str
) -> Generator[dict[str, Any], None, None]:
    """POST a user message to /run_sse and yield decoded ADK Event dicts
    as they arrive."""
    url = f"{AGENT_SERVICE_URL}/run_sse"
    payload = {
        "app_name": APP_NAME,
        "user_id": user_id,
        "session_id": session_id,
        "new_message": {"role": "user", "parts": [{"text": text}]},
        "streaming": True,
    }
    with requests.post(
        url,
        headers=_headers(),
        json=payload,
        stream=True,
        timeout=REQUEST_TIMEOUT_SECS,
    ) as resp:
        if resp.status_code != 200:
            raise AgentServiceError(
                f"Agent service returned {resp.status_code}: {resp.text[:500]}"
            )
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data:"):
                continue
            data_str = raw_line[len("data:") :].strip()
            if not data_str:
                continue
            try:
                yield json.loads(data_str)
            except json.JSONDecodeError:
                continue

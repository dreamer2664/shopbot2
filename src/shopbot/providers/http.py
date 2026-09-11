"""Shared HTTP helper for live providers.

Every provider injects a `session` (anything with .request()), so tests pass a
fake session and no network is ever touched. Real code passes requests.Session.

Status-code policy (uniform across all providers):
  429, 5xx  -> TransientError (retryable)
  4xx other -> PermanentError (bad creds/payload — retrying is pointless)
"""
from __future__ import annotations

from typing import Any

from .base import PermanentError, TransientError


def api_request(
    session: Any,
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json: Any = None,
    timeout: float = 30.0,
    label: str = "",
) -> Any:
    """Perform an HTTP call, mapping failures onto our error taxonomy."""
    try:
        resp = session.request(method, url, headers=headers, json=json,
                               timeout=timeout)
    except Exception as exc:  # connection error, DNS, TLS, timeout...
        raise TransientError(f"{label or url}: network error: {exc}") from exc

    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientError(
            f"{label or url}: HTTP {resp.status_code} {resp.text[:200]}")
    if resp.status_code >= 400:
        raise PermanentError(
            f"{label or url}: HTTP {resp.status_code} {resp.text[:200]}")

    if resp.status_code == 204 or not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        return resp.text

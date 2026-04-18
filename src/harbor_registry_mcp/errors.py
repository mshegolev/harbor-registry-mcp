"""Actionable error messages for Harbor Registry HTTP errors."""

from __future__ import annotations

import requests


class ConfigError(ValueError):
    """Raised when required environment variables are missing or malformed.

    Subclass of :class:`ValueError` so callers can continue to use
    ``isinstance(..., ValueError)``, but narrow enough that :func:`handle`
    can distinguish config errors from Pydantic validation errors bubbling
    up from tool input.
    """


def handle(exc: Exception, action: str) -> str:
    """Convert an exception raised while performing ``action`` into an
    LLM-readable string with a suggested next step.

    The goal is that the agent sees *why* the call failed and *what it could
    do about it* without needing to inspect a Python traceback.
    """
    if isinstance(exc, ConfigError):
        return (
            f"Error: configuration problem while {action} — {exc}. "
            "Check HARBOR_URL, HARBOR_USERNAME, HARBOR_PASSWORD, HARBOR_SSL_VERIFY environment variables."
        )

    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else None
        if code == 401:
            return (
                f"Error: authentication failed (HTTP 401) while {action}. "
                "Verify HARBOR_USERNAME + HARBOR_PASSWORD (robot token) are set and not expired."
            )
        if code == 403:
            return (
                f"Error: forbidden (HTTP 403) while {action}. "
                "The Harbor account lacks permission on the target project. "
                "Use a robot account with project-level read (or admin for cleanup) scope."
            )
        if code == 404:
            return (
                f"Error: resource not found (HTTP 404) while {action}. "
                "Check that the project / repository / reference exists. "
                "Use harbor_list_projects / harbor_list_repos for valid names."
            )
        if code == 409:
            return (
                f"Error: conflict (HTTP 409) while {action}. "
                "The artifact or tag is in use (referenced by a Helm chart, signature, etc.) "
                "or a concurrent operation is in flight. Retry after a short delay."
            )
        if code == 429:
            return (
                f"Error: rate-limited (HTTP 429) while {action}. "
                "Wait 30-60s before retrying; reduce page_size or batch fewer deletes."
            )
        if code is not None and 500 <= code < 600:
            return (
                f"Error: Harbor server error (HTTP {code}) while {action}. "
                "This is usually transient — retry in a few seconds; check Harbor /api/v2.0/health."
            )
        body = ""
        if exc.response is not None:
            try:
                body = exc.response.text[:200]
            except Exception:
                pass
        return f"Error: HTTP {code} while {action}. Response: {body}"

    if isinstance(exc, requests.ConnectionError):
        return f"Error: could not connect to Harbor while {action}. Check HARBOR_URL, network access, proxy settings."

    if isinstance(exc, requests.Timeout):
        return (
            f"Error: request timed out while {action}. "
            "Check network latency and retry; reduce page_size if pulling large artifact lists."
        )

    return f"Error: unexpected {type(exc).__name__} while {action}: {exc}"

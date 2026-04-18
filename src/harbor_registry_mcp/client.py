"""HTTP client for Harbor Registry REST API v2.0.

Thin wrapper around :mod:`requests` — reads config from env vars, adds
HTTP Basic auth, handles SSL-verify toggling, and exposes get/post/delete.
Errors bubble up as :class:`requests.HTTPError` and are mapped to
user-facing messages by :mod:`harbor_registry_mcp.errors`.

**Threading model.** The client uses ``requests`` (synchronous). FastMCP
runs synchronous ``@mcp.tool`` in a worker thread via
``anyio.to_thread.run_sync``, so blocking HTTP calls don't block the
asyncio event loop. Async tools inside this package explicitly wrap calls
with ``asyncio.to_thread``.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3
from requests.auth import HTTPBasicAuth

from harbor_registry_mcp.errors import ConfigError


def _parse_bool(value: str | bool | None, *, default: bool) -> bool:
    """Parse an env-var boolean.

    Accepts true/false/1/0/yes/no/on/off (case-insensitive). Returns
    ``default`` when ``value`` is ``None`` or empty.
    """
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "no", "off")


def _validate_url(url: str) -> str:
    """Validate that ``url`` is a well-formed HTTP/HTTPS URL.

    Returns the URL with leading/trailing whitespace and any trailing slash
    stripped. Raises :class:`ConfigError` if the URL is missing scheme/host
    or uses an unsupported scheme.
    """
    if not url:
        raise ConfigError("HARBOR_URL is not set — configure the env var")

    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ConfigError(f"HARBOR_URL must start with http:// or https:// (got: {url!r})")
    if not parsed.netloc:
        raise ConfigError(f"HARBOR_URL is missing host (got: {url!r})")
    return cleaned.rstrip("/")


class HarborClient:
    """Minimal Harbor Registry REST client.

    The client reads ``HARBOR_URL``, ``HARBOR_USERNAME``, ``HARBOR_PASSWORD``,
    ``HARBOR_SSL_VERIFY`` from the environment. Instances are safe to reuse
    — a single :class:`requests.Session` is kept for connection pooling.

    Args:
        url: Override ``HARBOR_URL`` env var. If ``None``, read from env.
        username: Override ``HARBOR_USERNAME``. If ``None``, read from env.
        password: Override ``HARBOR_PASSWORD``. If ``None``, read from env.
        ssl_verify: Override ``HARBOR_SSL_VERIFY``. If ``None``, read from env
            (accepts ``true``/``false``/``1``/``0``/``yes``/``no``, default ``True``).

    Raises:
        ConfigError: If required env vars are missing or URL malformed.
    """

    def __init__(
        self,
        url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        ssl_verify: bool | None = None,
    ) -> None:
        raw_url = url if url is not None else os.environ.get("HARBOR_URL", "")
        self.url = _validate_url(raw_url)
        self.api_url = f"{self.url}/api/v2.0"

        self.username = username if username is not None else os.environ.get("HARBOR_USERNAME", "")
        if not self.username:
            raise ConfigError("HARBOR_USERNAME is not set — configure the env var")

        self.password = password if password is not None else os.environ.get("HARBOR_PASSWORD", "")
        if not self.password:
            raise ConfigError("HARBOR_PASSWORD is not set — configure the env var")

        if ssl_verify is None:
            ssl_verify = _parse_bool(os.environ.get("HARBOR_SSL_VERIFY"), default=True)
        self.ssl_verify = ssl_verify

        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(self.username, self.password)
        self.session.verify = self.ssl_verify
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        # The MCP server is opinionated about proxies: Harbor is often a
        # corp service only reachable directly. Disable env-based proxy
        # discovery so the session doesn't hit 127.0.0.1:NNNN unexpectedly.
        self.session.trust_env = False

        if not self.ssl_verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> requests.Response:
        response = self.session.request(
            method=method,
            url=f"{self.api_url}{endpoint}",
            params=params,
            json=json_body,
            timeout=30,
        )
        response.raise_for_status()
        return response

    # ── Public API ──────────────────────────────────────────────────────────

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """GET ``{api_url}{endpoint}`` and return parsed JSON (usually a list/dict)."""
        response = self._request("GET", endpoint, params=params)
        # Some Harbor endpoints return 200 with empty body; guard against json decode error.
        if not response.content:
            return None
        return response.json()

    def get_all_pages(
        self,
        endpoint: str,
        *,
        page_size: int = 100,
        extra_params: dict[str, Any] | None = None,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch every page of a Harbor list endpoint and concatenate the results.

        Harbor list endpoints (projects, repositories, artifacts) paginate with
        ``page`` + ``page_size`` query parameters and stop returning rows once the
        caller walks past the last page.

        Args:
            endpoint: API path under ``/api/v2.0`` (leading slash required).
            page_size: Items per page (Harbor caps at 100).
            extra_params: Additional query params merged into every request.
            max_pages: Hard cap to prevent runaway loops on misbehaving servers.

        Returns:
            A single flat ``list`` with every row across all pages. Returns
            ``[]`` when the endpoint has no data.
        """
        results: list[dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            params: dict[str, Any] = {"page": page, "page_size": page_size}
            if extra_params:
                params.update(extra_params)
            chunk = self.get(endpoint, params=params) or []
            if not chunk:
                break
            results.extend(chunk)
            if len(chunk) < page_size:
                break
            page += 1
        return results

    def delete(self, endpoint: str) -> bool:
        """DELETE ``{api_url}{endpoint}``. Returns ``True`` on success."""
        self._request("DELETE", endpoint)
        return True

    def close(self) -> None:
        """Close the underlying HTTP session (called from lifespan on shutdown)."""
        self.session.close()


def encode_repo(repository_name: str) -> str:
    """URL-encode a Harbor repository name.

    Harbor repos can contain slashes (e.g. ``nginx-proxy/nginx``); its REST
    API expects them percent-encoded as ``%2F`` inside the path.
    """
    return repository_name.replace("/", "%2F")


def size_human(size_bytes: int) -> str:
    """Format a byte count as a human-readable string (GB / MB / KB / B)."""
    if size_bytes >= 1024**3:
        return f"{size_bytes / 1024**3:.2f} GB"
    if size_bytes >= 1024**2:
        return f"{size_bytes / 1024**2:.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"

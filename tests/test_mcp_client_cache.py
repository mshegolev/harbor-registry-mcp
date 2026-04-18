"""Tests for the module-level client cache in :mod:`harbor_registry_mcp._mcp`.

``get_client`` lazily instantiates a single :class:`HarborClient` protected
by a lock (double-checked locking). This test covers the happy path —
repeated calls return the *same* instance, and the lifespan hook clears
the cache so a subsequent call rebuilds the client.
"""

from __future__ import annotations

import pytest

from harbor_registry_mcp import _mcp
from harbor_registry_mcp.client import HarborClient


@pytest.fixture(autouse=True)
def reset_cache() -> None:
    """Clear the module-global client between tests to avoid test-order coupling."""
    with _mcp._client_lock:
        if _mcp._client is not None:
            try:
                _mcp._client.close()
            except Exception:
                pass
        _mcp._client = None


def test_get_client_returns_same_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARBOR_URL", "https://harbor.example.com")
    monkeypatch.setenv("HARBOR_USERNAME", "u")
    monkeypatch.setenv("HARBOR_PASSWORD", "p")  # pragma: allowlist secret
    first = _mcp.get_client()
    second = _mcp.get_client()
    assert first is second
    assert isinstance(first, HarborClient)


def test_get_client_raises_on_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARBOR_URL", raising=False)
    monkeypatch.delenv("HARBOR_USERNAME", raising=False)
    monkeypatch.delenv("HARBOR_PASSWORD", raising=False)
    with pytest.raises(Exception, match="HARBOR_URL"):
        _mcp.get_client()


def test_cache_rebuilds_after_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARBOR_URL", "https://harbor.example.com")
    monkeypatch.setenv("HARBOR_USERNAME", "u")
    monkeypatch.setenv("HARBOR_PASSWORD", "p")  # pragma: allowlist secret
    first = _mcp.get_client()
    with _mcp._client_lock:
        _mcp._client = None
    second = _mcp.get_client()
    assert first is not second

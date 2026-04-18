"""Unit tests for pure helpers in :mod:`harbor_registry_mcp.client`.

These tests avoid the network entirely — they cover env-var parsing, URL
validation, repository-name encoding, byte-size formatting, and the
``HarborClient.__init__`` configuration branch (which raises
:class:`ConfigError` when required env vars are missing).
"""

from __future__ import annotations

import pytest

from harbor_registry_mcp.client import (
    HarborClient,
    _parse_bool,
    _validate_url,
    encode_repo,
    size_human,
)
from harbor_registry_mcp.errors import ConfigError


class TestParseBool:
    @pytest.mark.parametrize("value", ["true", "True", "1", "yes", "on", "YES"])
    def test_truthy_strings(self, value: str) -> None:
        assert _parse_bool(value, default=False) is True

    @pytest.mark.parametrize("value", ["false", "False", "0", "no", "off", "OFF"])
    def test_falsy_strings(self, value: str) -> None:
        assert _parse_bool(value, default=True) is False

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_returns_default(self, value: str | None) -> None:
        assert _parse_bool(value, default=True) is True
        assert _parse_bool(value, default=False) is False

    def test_bool_passthrough(self) -> None:
        assert _parse_bool(True, default=False) is True
        assert _parse_bool(False, default=True) is False


class TestValidateUrl:
    def test_strips_trailing_slash(self) -> None:
        assert _validate_url("https://harbor.example.com/") == "https://harbor.example.com"

    def test_strips_whitespace(self) -> None:
        assert _validate_url("  https://harbor.example.com  ") == "https://harbor.example.com"

    def test_preserves_no_trailing_slash(self) -> None:
        assert _validate_url("https://harbor.example.com") == "https://harbor.example.com"

    def test_http_scheme_allowed(self) -> None:
        assert _validate_url("http://harbor.local") == "http://harbor.local"

    def test_empty_raises(self) -> None:
        with pytest.raises(ConfigError, match="HARBOR_URL is not set"):
            _validate_url("")

    def test_missing_scheme_raises(self) -> None:
        with pytest.raises(ConfigError, match="http:// or https://"):
            _validate_url("harbor.example.com")

    def test_wrong_scheme_raises(self) -> None:
        with pytest.raises(ConfigError, match="http:// or https://"):
            _validate_url("ftp://harbor.example.com")

    def test_missing_host_raises(self) -> None:
        with pytest.raises(ConfigError, match="missing host"):
            _validate_url("https://")


class TestEncodeRepo:
    def test_no_slash_passthrough(self) -> None:
        assert encode_repo("nginx") == "nginx"

    def test_single_slash(self) -> None:
        assert encode_repo("nginx-proxy/nginx") == "nginx-proxy%2Fnginx"

    def test_multiple_slashes(self) -> None:
        assert encode_repo("a/b/c") == "a%2Fb%2Fc"

    def test_empty(self) -> None:
        assert encode_repo("") == ""


class TestSizeHuman:
    def test_bytes(self) -> None:
        assert size_human(0) == "0 B"
        assert size_human(512) == "512 B"
        assert size_human(1023) == "1023 B"

    def test_kilobytes(self) -> None:
        assert size_human(1024) == "1.0 KB"
        assert size_human(1536) == "1.5 KB"

    def test_megabytes(self) -> None:
        assert size_human(1024 * 1024) == "1.0 MB"
        assert size_human(int(2.5 * 1024 * 1024)) == "2.5 MB"

    def test_gigabytes(self) -> None:
        assert size_human(1024**3) == "1.00 GB"
        assert size_human(int(3.14 * 1024**3)) == "3.14 GB"


class TestHarborClientInit:
    def test_missing_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HARBOR_URL", raising=False)
        monkeypatch.setenv("HARBOR_USERNAME", "u")
        monkeypatch.setenv("HARBOR_PASSWORD", "p")  # pragma: allowlist secret
        with pytest.raises(ConfigError, match="HARBOR_URL"):
            HarborClient()

    def test_missing_username_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HARBOR_URL", "https://harbor.example.com")
        monkeypatch.delenv("HARBOR_USERNAME", raising=False)
        monkeypatch.setenv("HARBOR_PASSWORD", "p")  # pragma: allowlist secret
        with pytest.raises(ConfigError, match="HARBOR_USERNAME"):
            HarborClient()

    def test_missing_password_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HARBOR_URL", "https://harbor.example.com")
        monkeypatch.setenv("HARBOR_USERNAME", "u")
        monkeypatch.delenv("HARBOR_PASSWORD", raising=False)
        with pytest.raises(ConfigError, match="HARBOR_PASSWORD"):
            HarborClient()

    def test_happy_path_builds_api_url_and_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HARBOR_URL", "https://harbor.example.com/")
        monkeypatch.setenv("HARBOR_USERNAME", "robot$ci")
        monkeypatch.setenv("HARBOR_PASSWORD", "secret")  # pragma: allowlist secret
        monkeypatch.setenv("HARBOR_SSL_VERIFY", "false")
        client = HarborClient()
        try:
            assert client.url == "https://harbor.example.com"
            assert client.api_url == "https://harbor.example.com/api/v2.0"
            assert client.username == "robot$ci"
            assert client.ssl_verify is False
            assert client.session.trust_env is False
            assert client.session.headers["Accept"] == "application/json"
        finally:
            client.close()

    def test_overrides_take_precedence_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HARBOR_URL", "https://env.example.com")
        monkeypatch.setenv("HARBOR_USERNAME", "env-user")
        monkeypatch.setenv("HARBOR_PASSWORD", "env-pass")  # pragma: allowlist secret
        client = HarborClient(
            url="https://explicit.example.com",
            username="explicit-user",
            password="explicit-pass",  # pragma: allowlist secret
            ssl_verify=True,
        )
        try:
            assert client.url == "https://explicit.example.com"
            assert client.username == "explicit-user"
        finally:
            client.close()

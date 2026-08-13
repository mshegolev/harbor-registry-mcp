"""Unit tests for :mod:`harbor_registry_mcp.tools`.

Two groups:

- the pure shaping helpers, which take raw Harbor API dicts and shape them
  into the TypedDict output schemas — no I/O, exercised directly;
- the dry-run guard of ``harbor_delete_untagged``, which *is* about I/O:
  the point is that in dry-run the tool issues no delete call at all. A stub
  client records every ``delete()`` so the test can assert the count is zero
  rather than trusting the response text.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from harbor_registry_mcp import tools
from harbor_registry_mcp.tools import _public, _pull_time, _shape_artifact, _short


class TestShort:
    def test_trims_to_19(self) -> None:
        assert _short("2026-04-18T12:34:56.789+00:00") == "2026-04-18T12:34:56"

    def test_shorter_than_limit_is_unchanged(self) -> None:
        assert _short("abc") == "abc"

    def test_custom_length(self) -> None:
        assert _short("abcdefg", n=3) == "abc"

    def test_none_returns_none(self) -> None:
        assert _short(None) is None

    def test_empty_returns_none(self) -> None:
        assert _short("") is None


class TestPullTime:
    def test_normal_timestamp(self) -> None:
        assert _pull_time("2026-04-18T12:00:00.000Z") == "2026-04-18T12:00:00"

    def test_never_pulled_sentinel_becomes_none(self) -> None:
        assert _pull_time("0001-01-01T00:00:00.000Z") is None

    def test_none(self) -> None:
        assert _pull_time(None) is None

    def test_empty_string(self) -> None:
        assert _pull_time("") is None


class TestPublic:
    def test_true_string(self) -> None:
        assert _public({"public": "true"}) is True

    def test_false_string(self) -> None:
        assert _public({"public": "false"}) is False

    def test_missing_key_defaults_false(self) -> None:
        assert _public({"other": "x"}) is False

    def test_none_metadata_defaults_false(self) -> None:
        assert _public(None) is False

    def test_boolean_value_treated_as_not_public(self) -> None:
        # Harbor encodes as string "true"; a literal True should NOT be treated
        # as public (we want strict string comparison).
        assert _public({"public": True}) is False


class TestShapeArtifact:
    def test_full_artifact(self) -> None:
        raw = {
            "digest": "sha256:abc123def456" + "0" * 40,
            "size": 52_428_800,  # 50 MB
            "push_time": "2026-04-18T12:34:56.789Z",
            "pull_time": "2026-04-18T13:00:00.000Z",
            "tags": [{"name": "v1.0"}, {"name": "latest"}],
            "scan_overview": {
                "application/vnd.security.vulnerability.report; version=1.1": {
                    "scan_status": "Success",
                    "summary": {"summary": {"Critical": 1, "High": 3, "Medium": 7}},
                }
            },
        }
        shaped = _shape_artifact(raw)
        assert shaped["tags"] == ["v1.0", "latest"]
        assert shaped["digest"].startswith("sha256:")
        assert shaped["size"] == "50.0 MB"
        assert shaped["size_bytes"] == 52_428_800
        assert shaped["push_time"] == "2026-04-18T12:34:56"
        assert shaped["pull_time"] == "2026-04-18T13:00:00"
        assert shaped["scan_status"] == "Success"
        assert shaped["vulnerabilities"] == {"Critical": 1, "High": 3, "Medium": 7}

    def test_untagged_never_pulled(self) -> None:
        raw = {
            "digest": "sha256:xyz",
            "size": 1024,
            "push_time": "2026-04-18T00:00:00.000Z",
            "pull_time": "0001-01-01T00:00:00.000Z",
            "tags": None,
        }
        shaped = _shape_artifact(raw)
        assert shaped["tags"] == []
        assert shaped["pull_time"] is None
        assert shaped["size"] == "1.0 KB"
        assert shaped["scan_status"] is None
        assert shaped["vulnerabilities"] is None

    def test_missing_size_defaults_zero(self) -> None:
        shaped = _shape_artifact({"digest": "sha256:0"})
        assert shaped["size_bytes"] == 0
        assert shaped["size"] == "0 B"
        assert shaped["tags"] == []
        assert shaped["push_time"] is None
        assert shaped["pull_time"] is None

    def test_tag_without_name_is_dropped(self) -> None:
        raw = {
            "digest": "sha256:y",
            "size": 0,
            "tags": [{"name": "v1"}, {}, {"name": ""}],
        }
        # The helper uses a truthy check (``if t.get("name")``) so empty
        # strings are dropped along with missing keys.
        shaped = _shape_artifact(raw)
        assert shaped["tags"] == ["v1"]

    def test_scan_overview_without_summary(self) -> None:
        raw = {
            "digest": "sha256:z",
            "size": 0,
            "scan_overview": {"some-key": {"scan_status": "Pending"}},
        }
        shaped = _shape_artifact(raw)
        assert shaped["scan_status"] == "Pending"
        assert shaped["vulnerabilities"] is None

    def test_push_time_sentinel_is_normalized(self) -> None:
        # Rare but possible: manifest-only upload with a zero push_time.
        # After F6 we funnel push_time through _normalize_ts, so the sentinel
        # collapses to None rather than leaking the weird year-0001 string.
        shaped = _shape_artifact(
            {
                "digest": "sha256:aa",
                "size": 1,
                "push_time": "0001-01-01T00:00:00.000Z",
            }
        )
        assert shaped["push_time"] is None


# ── harbor_delete_untagged: the dry-run guard ──────────────────────────────
#
# ``harbor_delete_untagged`` sweeps every repository of a project when
# ``repository_name`` is omitted — the widest blast radius in the catalogue.
# Until 0.2.0 it had no dry_run at all while its milder neighbour
# ``harbor_delete_old_artifacts`` did. These tests hold the guard in place at
# the only level that matters: whether a DELETE actually leaves the process.


class _StubClient:
    """Minimal stand-in for :class:`HarborClient` that records deletes."""

    def __init__(self, repos: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> None:
        self._repos = repos
        self._artifacts = artifacts
        self.deleted_paths: list[str] = []

    def get_all_pages(
        self,
        endpoint: str,
        *,
        page_size: int = 100,
        extra_params: dict[str, Any] | None = None,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        return self._artifacts if "/artifacts" in endpoint else self._repos

    def delete(self, endpoint: str) -> dict[str, Any]:
        self.deleted_paths.append(endpoint)
        return {}


class _NullContext:
    """MCP ``Context`` stub — swallows progress/info events."""

    async def report_progress(self, progress: float, message: str | None = None) -> None:
        return None

    async def info(self, message: str) -> None:
        return None


REPOS = [{"name": "demo/app"}]
ARTIFACTS = [
    {"digest": "sha256:tagged0", "size": 100, "tags": [{"name": "v1.0"}]},
    {"digest": "sha256:orphan1", "size": 1024, "tags": []},
    {"digest": "sha256:orphan2", "size": 2048, "tags": None},
]


@pytest.fixture
def stub_client(monkeypatch: pytest.MonkeyPatch) -> _StubClient:
    client = _StubClient(REPOS, ARTIFACTS)
    monkeypatch.setattr(tools, "get_client", lambda: client)
    return client


def _sweep(**kwargs: Any) -> dict[str, Any]:
    """Invoke the tool and return its structured payload."""
    result = asyncio.run(tools.harbor_delete_untagged("demo", _NullContext(), **kwargs))  # type: ignore[arg-type]
    return dict(result.structuredContent or {})


class TestDeleteUntaggedDryRun:
    def test_default_call_deletes_nothing(self, stub_client: _StubClient) -> None:
        """No ``dry_run`` argument at all must still be a no-op.

        This is the whole point of the default: an agent that calls the tool
        the obvious way does not destroy anything.
        """
        payload = _sweep()

        assert stub_client.deleted_paths == [], "dry-run issued a DELETE to Harbor"
        assert payload["dry_run"] is True

    def test_dry_run_reports_candidates_without_deleting(self, stub_client: _StubClient) -> None:
        payload = _sweep(dry_run=True)

        assert stub_client.deleted_paths == [], "dry-run issued a DELETE to Harbor"
        assert payload["deleted_count"] == 2, "both untagged artifacts should be reported as candidates"
        assert [d["deleted"] for d in payload["deleted"]] == [None, None], "candidates must not claim to be deleted"
        assert payload["freed_bytes"] == 3072, "dry-run must still report the space that would be freed"
        assert payload["errors"] == []
        assert payload["hint"] == "Re-run with dry_run=False to perform the deletion."

    def test_dry_run_false_actually_deletes_untagged_only(self, stub_client: _StubClient) -> None:
        payload = _sweep(dry_run=False)

        assert len(stub_client.deleted_paths) == 2, f"expected 2 deletes, got {stub_client.deleted_paths}"
        assert all("orphan" in path for path in stub_client.deleted_paths), (
            f"a tagged artifact was deleted: {stub_client.deleted_paths}"
        )
        assert payload["dry_run"] is False
        assert payload["deleted_count"] == 2
        assert [d["deleted"] for d in payload["deleted"]] == [True, True]
        assert payload["freed_bytes"] == 3072
        assert payload["hint"] is None

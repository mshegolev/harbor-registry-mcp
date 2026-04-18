"""Unit tests for pure shaping helpers in :mod:`harbor_registry_mcp.tools`.

These functions take raw Harbor API dicts and shape them into the
TypedDict output schemas. They have no I/O, so we exercise them directly
without mocking any HTTP client.
"""

from __future__ import annotations

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

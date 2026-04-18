"""Tests for :meth:`HarborClient.get_all_pages`.

Harbor list endpoints paginate via ``page`` + ``page_size``. The helper
must walk the pages until it sees a short page (or an empty one) and
concatenate every row it received.
"""

from __future__ import annotations

import pytest
import responses

from harbor_registry_mcp.client import HarborClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> HarborClient:
    monkeypatch.setenv("HARBOR_URL", "https://harbor.example.com")
    monkeypatch.setenv("HARBOR_USERNAME", "u")
    monkeypatch.setenv("HARBOR_PASSWORD", "p")  # pragma: allowlist secret
    c = HarborClient()
    # ``get_all_pages`` uses the Session; ``responses`` patches urllib3 globally,
    # and ``trust_env=False`` means no proxy interposes.
    yield c
    c.close()


@responses.activate
def test_walks_until_short_page(client: HarborClient) -> None:
    base = "https://harbor.example.com/api/v2.0/projects"
    responses.add(responses.GET, base, json=[{"n": i} for i in range(3)], status=200)
    responses.add(responses.GET, base, json=[{"n": i} for i in range(3, 6)], status=200)
    responses.add(responses.GET, base, json=[{"n": i} for i in range(6, 8)], status=200)

    out = client.get_all_pages("/projects", page_size=3)
    assert [r["n"] for r in out] == [0, 1, 2, 3, 4, 5, 6, 7]
    # Confirm all three pages were fetched with correct page param.
    calls = [call.request.url for call in responses.calls]
    assert any("page=1" in u and "page_size=3" in u for u in calls)
    assert any("page=2" in u for u in calls)
    assert any("page=3" in u for u in calls)


@responses.activate
def test_stops_on_empty_page(client: HarborClient) -> None:
    base = "https://harbor.example.com/api/v2.0/repositories"
    responses.add(responses.GET, base, json=[{"x": 1}, {"x": 2}], status=200)
    responses.add(responses.GET, base, json=[], status=200)

    out = client.get_all_pages("/repositories", page_size=2)
    assert len(out) == 2
    assert len(responses.calls) == 2  # stops immediately after empty page


@responses.activate
def test_passes_extra_params(client: HarborClient) -> None:
    base = "https://harbor.example.com/api/v2.0/projects/p/repositories/r/artifacts"
    responses.add(responses.GET, base, json=[{"digest": "sha256:a"}], status=200)

    out = client.get_all_pages(
        "/projects/p/repositories/r/artifacts",
        page_size=100,
        extra_params={"with_tag": True, "with_scan_overview": True},
    )
    assert out == [{"digest": "sha256:a"}]
    url = responses.calls[0].request.url
    assert "with_tag=True" in url
    assert "with_scan_overview=True" in url


@responses.activate
def test_returns_empty_when_first_page_empty(client: HarborClient) -> None:
    responses.add(
        responses.GET,
        "https://harbor.example.com/api/v2.0/projects",
        json=[],
        status=200,
    )
    assert client.get_all_pages("/projects", page_size=10) == []


@responses.activate
def test_max_pages_cap(client: HarborClient) -> None:
    base = "https://harbor.example.com/api/v2.0/projects"
    # Always return a full page → never signals "last page"; cap stops the loop.
    responses.add(responses.GET, base, json=[{"n": 0}, {"n": 1}], status=200)
    responses.add(responses.GET, base, json=[{"n": 2}, {"n": 3}], status=200)
    responses.add(responses.GET, base, json=[{"n": 4}, {"n": 5}], status=200)

    out = client.get_all_pages("/projects", page_size=2, max_pages=3)
    assert len(out) == 6
    assert len(responses.calls) == 3

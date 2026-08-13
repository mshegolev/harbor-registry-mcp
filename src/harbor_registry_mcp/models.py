"""TypedDict output schemas for every MCP tool.

These schemas are read by FastMCP (``structured_output=True``) to generate
a JSON-Schema ``outputSchema`` for each tool. Clients that support
structured data use that schema to validate the ``structuredContent``
payload; clients that don't use the markdown ``content`` block instead.

**Python / Pydantic compat note.** We deliberately avoid
``Required`` / ``NotRequired`` qualifiers: Pydantic 2.13+ mishandles them
during runtime schema generation on Py < 3.12 (see
https://errors.pydantic.dev/2.13/u/typed-dict-version). Optional fields use
``| None`` convention; the code always sets the key (``None`` when absent).
"""

from __future__ import annotations

import sys

if sys.version_info >= (3, 12):
    from typing import TypedDict
else:
    from typing_extensions import TypedDict


# ── Projects ────────────────────────────────────────────────────────────────


class ProjectSummary(TypedDict):
    name: str
    project_id: int
    repo_count: int
    public: bool
    updated: str | None


class ProjectsListOutput(TypedDict):
    projects_count: int
    page: int
    page_size: int
    has_more: bool
    next_page: int | None
    projects: list[ProjectSummary]


# ── Repositories ────────────────────────────────────────────────────────────


class RepositorySummary(TypedDict):
    name: str
    artifact_count: int
    pull_count: int
    updated: str | None


class RepositoriesListOutput(TypedDict):
    project: str
    repositories_count: int
    page: int
    page_size: int
    has_more: bool
    next_page: int | None
    repositories: list[RepositorySummary]


# ── Artifacts ───────────────────────────────────────────────────────────────


class ArtifactSummary(TypedDict):
    tags: list[str]
    digest: str
    size: str
    size_bytes: int
    push_time: str | None
    pull_time: str | None
    scan_status: str | None
    vulnerabilities: dict[str, int] | None


class ArtifactsListOutput(TypedDict):
    project: str
    repository: str
    total_size: str
    total_size_bytes: int
    artifacts_count: int
    page: int
    page_size: int
    has_more: bool
    next_page: int | None
    artifacts: list[ArtifactSummary]


# ── Storage report ──────────────────────────────────────────────────────────


class StorageRepoReport(TypedDict):
    repository: str
    artifact_count: int
    pull_count: int
    size: str
    size_bytes: int
    artifacts: list[ArtifactSummary]


class StorageReportOutput(TypedDict):
    project: str
    total_repositories: int
    total_size: str
    total_size_bytes: int
    repositories: list[StorageRepoReport]


# ── Cleanup candidates ──────────────────────────────────────────────────────


class CleanupCandidate(TypedDict):
    repository: str
    tags: list[str]
    digest: str
    size: str
    size_bytes: int
    push_time: str | None
    reasons: list[str]


class CleanupCandidatesOutput(TypedDict):
    project: str
    candidates_count: int
    total_reclaimable: str
    total_reclaimable_bytes: int
    candidates: list[CleanupCandidate]
    hint: str


# ── Delete results ──────────────────────────────────────────────────────────


class DeleteArtifactOutput(TypedDict):
    """Result of :func:`harbor_delete_artifact`.

    ``success`` is ``False`` when the artifact was not found before delete;
    ``error`` carries an explanation in that case, else is ``None``.
    """

    success: bool
    project: str
    repository: str
    reference: str
    deleted_tags: list[str]
    freed_size: str
    freed_bytes: int
    error: str | None


class DeletedArtifact(TypedDict):
    """One untagged artifact touched by :func:`harbor_delete_untagged`.

    ``deleted`` mirrors :class:`ToDeleteArtifact`: ``True`` after a real
    delete, ``None`` in dry-run (i.e. "this is what *would* go").
    """

    repository: str
    digest: str
    size: str
    deleted: bool | None


class DeleteErrorItem(TypedDict):
    repository: str
    digest: str
    error: str


class DeleteUntaggedOutput(TypedDict):
    """Result of :func:`harbor_delete_untagged`.

    ``dry_run=True`` means nothing was deleted — every item in ``deleted``
    has ``deleted=None``, ``deleted_count`` counts *candidates*, and
    ``freed_*`` reflects hypothetical space reclaim. ``errors`` can only be
    non-empty on a real run, since dry-run issues no delete calls at all.
    """

    project: str
    dry_run: bool
    repos_scanned: list[str]
    deleted_count: int
    freed_size: str
    freed_bytes: int
    deleted: list[DeletedArtifact]
    errors: list[DeleteErrorItem]
    hint: str | None


class KeptArtifact(TypedDict):
    tags: list[str]
    digest: str
    push_time: str | None


class ToDeleteArtifact(TypedDict):
    tags: list[str]
    digest: str
    size: str
    push_time: str | None
    deleted: bool | None


class DeleteOldOutput(TypedDict):
    """Result of :func:`harbor_delete_old_artifacts`.

    ``dry_run=True`` means no artifacts were actually deleted — every item in
    ``to_delete`` has ``deleted=None`` and ``freed_*`` reflects hypothetical
    space reclaim. ``message`` is set when nothing matched.
    """

    project: str
    repository: str
    dry_run: bool
    keeping: list[KeptArtifact]
    to_delete_count: int
    freed_size: str
    freed_bytes: int
    to_delete: list[ToDeleteArtifact]
    hint: str | None
    message: str | None

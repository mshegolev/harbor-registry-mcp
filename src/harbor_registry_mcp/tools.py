"""MCP tools for Harbor Registry.

8 tools — 5 read-only (discovery + reports + cleanup planning), 3
destructive (delete operations, ``destructiveHint: True``). Bulk delete
defaults to dry-run.

**Threading model.** All tools are synchronous ``def``. FastMCP runs them
in a worker thread via ``anyio.to_thread.run_sync``, so blocking HTTP
calls don't block the asyncio event loop.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from mcp.server.fastmcp import Context
from pydantic import Field

from harbor_registry_mcp import output
from harbor_registry_mcp._mcp import get_client, mcp
from harbor_registry_mcp.client import encode_repo, size_human
from harbor_registry_mcp.models import (
    ArtifactsListOutput,
    ArtifactSummary,
    CleanupCandidate,
    CleanupCandidatesOutput,
    DeleteArtifactOutput,
    DeletedArtifact,
    DeleteErrorItem,
    DeleteOldOutput,
    DeleteUntaggedOutput,
    KeptArtifact,
    ProjectsListOutput,
    ProjectSummary,
    RepositoriesListOutput,
    RepositorySummary,
    StorageRepoReport,
    StorageReportOutput,
    ToDeleteArtifact,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _short(s: str | None, n: int = 19) -> str | None:
    """Return ``s[:n]`` or ``None``."""
    return s[:n] if s else None


def _normalize_ts(s: str | None) -> str | None:
    """Normalise a Harbor timestamp.

    Harbor returns ``0001-01-01T00:00:00.000Z`` as a "never" sentinel (for
    ``pull_time`` of unused artifacts and, rarely, for ``push_time`` on
    manifest-only uploads). We collapse both the empty string and the
    sentinel to ``None`` so downstream consumers can treat "no value"
    uniformly.
    """
    if not s or s.startswith("0001"):
        return None
    return s[:19]


# Backwards-compatible alias retained for the existing unit tests.
_pull_time = _normalize_ts


def _public(metadata: dict[str, Any] | None) -> bool:
    """Harbor encodes project visibility as ``metadata.public`` string `"true"`/`"false"`."""
    return (metadata or {}).get("public", "false") == "true"


def _shape_artifact(a: dict[str, Any]) -> ArtifactSummary:
    """Convert Harbor's artifact JSON into :class:`ArtifactSummary`.

    Extracts tag list, scan status / vulnerabilities from the first
    available scanner in ``scan_overview``.
    """
    tags = [t["name"] for t in (a.get("tags") or []) if t.get("name")]
    scan_status: str | None = None
    vulnerabilities: dict[str, int] | None = None
    overview = a.get("scan_overview") or {}
    if overview:
        first = next(iter(overview.values()), {})
        scan_status = first.get("scan_status")
        summary = (first.get("summary") or {}).get("summary") or {}
        vulnerabilities = summary or None

    size_bytes = int(a.get("size", 0) or 0)
    return {
        "tags": tags or [],
        "digest": a.get("digest", ""),
        "size": size_human(size_bytes),
        "size_bytes": size_bytes,
        "push_time": _normalize_ts(a.get("push_time")),
        "pull_time": _normalize_ts(a.get("pull_time")),
        "scan_status": scan_status,
        "vulnerabilities": vulnerabilities,
    }


def _list_artifacts(client: Any, project_name: str, repository_name: str) -> list[dict[str, Any]]:
    """Fetch every artifact for a repository across all pages."""
    return client.get_all_pages(
        f"/projects/{project_name}/repositories/{encode_repo(repository_name)}/artifacts",
        page_size=100,
        extra_params={"with_tag": True, "with_scan_overview": True},
    )


def _list_repos(client: Any, project_name: str) -> list[dict[str, Any]]:
    """Fetch every repository for a project across all pages."""
    return client.get_all_pages(
        f"/projects/{project_name}/repositories",
        page_size=100,
    )


async def _report(ctx: Context, progress: float, message: str) -> None:
    """Emit a combined MCP progress + info event."""
    await ctx.report_progress(progress, message=message)
    await ctx.info(message)


# ── Discovery tools ────────────────────────────────────────────────────────


@mcp.tool(
    name="harbor_list_projects",
    annotations={
        "title": "List Projects",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
def harbor_list_projects(
    page: Annotated[int, Field(default=1, ge=1, le=1000, description="Page number (1-based).")] = 1,
    page_size: Annotated[int, Field(default=50, ge=1, le=100, description="Items per page (1-100).")] = 50,
) -> ProjectsListOutput:
    """List Harbor projects, sorted by repository count (descending *within the page*).

    Use this first to discover which Harbor projects exist before drilling in
    with ``harbor_list_repos`` / ``harbor_list_artifacts``.

    Pagination: if ``has_more`` is ``True``, call again with ``page + 1``.
    Note that sorting is per-page — agents that need a global ranking should
    aggregate across pages.

    Returns:
        dict with keys ``projects_count`` / ``page`` / ``page_size`` /
        ``has_more`` / ``next_page`` / ``projects`` (list).
    """
    try:
        client = get_client()
        raw = client.get("/projects", params={"page": page, "page_size": page_size}) or []
        projects: list[ProjectSummary] = sorted(
            (
                {
                    "name": p["name"],
                    "project_id": int(p["project_id"]),
                    "repo_count": int(p.get("repo_count", 0) or 0),
                    "public": _public(p.get("metadata")),
                    "updated": _short(p.get("update_time")),
                }
                for p in raw
            ),
            key=lambda p: p["repo_count"],
            reverse=True,
        )
        has_more = len(raw) == page_size
        result: ProjectsListOutput = {
            "projects_count": len(projects),
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
            "next_page": page + 1 if has_more else None,
            "projects": projects,
        }
        md = "\n".join([f"- **{p['name']}** ({p['repo_count']} repos, public={p['public']})" for p in projects])
        heading = f"## Projects — page {page} ({len(projects)} rows, has_more={has_more})"
        return output.ok(result, f"{heading}\n\n{md}")  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, "listing projects")


@mcp.tool(
    name="harbor_list_repos",
    annotations={
        "title": "List Repositories",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
def harbor_list_repos(
    project_name: Annotated[str, Field(min_length=1, max_length=255, description="Harbor project name.")],
    page: Annotated[int, Field(default=1, ge=1, le=1000, description="Page number (1-based).")] = 1,
    page_size: Annotated[int, Field(default=50, ge=1, le=100, description="Items per page (1-100).")] = 50,
) -> RepositoriesListOutput:
    """List repositories in a Harbor project.

    Each repository is reported with artifact count and total pull count
    (useful for spotting unused repos before cleanup).

    Pagination: if ``has_more`` is ``True``, call again with ``page + 1``.
    """
    try:
        client = get_client()
        raw = (
            client.get(
                f"/projects/{project_name}/repositories",
                params={"page": page, "page_size": page_size},
            )
            or []
        )
        repos: list[RepositorySummary] = [
            {
                "name": r["name"].replace(f"{project_name}/", ""),
                "artifact_count": int(r.get("artifact_count", 0) or 0),
                "pull_count": int(r.get("pull_count", 0) or 0),
                "updated": _short(r.get("update_time")),
            }
            for r in raw
        ]
        has_more = len(raw) == page_size
        result: RepositoriesListOutput = {
            "project": project_name,
            "repositories_count": len(repos),
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
            "next_page": page + 1 if has_more else None,
            "repositories": repos,
        }
        md = f"## {project_name} — page {page} ({len(repos)} repos, has_more={has_more})\n\n" + "\n".join(
            [f"- **{r['name']}** — {r['artifact_count']} artifacts, {r['pull_count']} pulls" for r in repos]
        )
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"listing repositories in {project_name}")


@mcp.tool(
    name="harbor_list_artifacts",
    annotations={
        "title": "List Artifacts",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
def harbor_list_artifacts(
    project_name: Annotated[str, Field(min_length=1, max_length=255, description="Harbor project name.")],
    repository_name: Annotated[
        str, Field(min_length=1, max_length=255, description="Repository name within the project.")
    ],
    page: Annotated[int, Field(default=1, ge=1, le=1000, description="Page number (1-based).")] = 1,
    page_size: Annotated[int, Field(default=50, ge=1, le=100, description="Items per page (1-100).")] = 50,
) -> ArtifactsListOutput:
    """List artifacts (tags) in a repository, newest first.

    Each artifact carries digest, size, push/pull timestamps, scan status
    and vulnerability counts (if scanned).

    Pagination: if ``has_more`` is ``True``, call again with ``page + 1``.
    For repositories with hundreds of artifacts prefer ``harbor_storage_report``
    or ``harbor_cleanup_candidates`` which paginate internally.
    """
    try:
        client = get_client()
        raw = (
            client.get(
                f"/projects/{project_name}/repositories/{encode_repo(repository_name)}/artifacts",
                params={
                    "page": page,
                    "page_size": page_size,
                    "with_tag": True,
                    "with_scan_overview": True,
                },
            )
            or []
        )
        artifacts: list[ArtifactSummary] = [
            _shape_artifact(a) for a in sorted(raw, key=lambda a: a.get("push_time") or "", reverse=True)
        ]
        total_bytes = sum(a["size_bytes"] for a in artifacts)
        has_more = len(raw) == page_size
        result: ArtifactsListOutput = {
            "project": project_name,
            "repository": repository_name,
            "total_size": size_human(total_bytes),
            "total_size_bytes": total_bytes,
            "artifacts_count": len(artifacts),
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
            "next_page": page + 1 if has_more else None,
            "artifacts": artifacts,
        }
        md = (
            f"## {project_name}/{repository_name} ({len(artifacts)} artifacts, {size_human(total_bytes)})\n\n"
            + "\n".join(
                [
                    f"- **{','.join(a['tags']) or '(untagged)'}** — {a['size']} pushed {a['push_time']}"
                    for a in artifacts
                ]
            )
        )
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"listing artifacts in {project_name}/{repository_name}")


@mcp.tool(
    name="harbor_storage_report",
    annotations={
        "title": "Storage Report",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
async def harbor_storage_report(
    project_name: Annotated[str, Field(min_length=1, max_length=255, description="Harbor project name.")],
    ctx: Context,
) -> StorageReportOutput:
    """Full storage breakdown for a Harbor project.

    Iterates every repository × every artifact and returns a sorted-by-size
    report — the canonical view for "what's eating up our quota?". Performs
    O(repos × artifacts) API calls; emits progress events through MCP Context.
    """
    try:
        client = get_client()
        await _report(ctx, 0.05, f"listing repositories in {project_name}")
        repos = await asyncio.to_thread(_list_repos, client, project_name)
        repo_reports: list[StorageRepoReport] = []
        total_bytes = 0
        for i, repo in enumerate(repos):
            short_name = repo["name"].replace(f"{project_name}/", "")
            await _report(ctx, 0.1 + 0.85 * (i / max(len(repos), 1)), f"scanning {short_name}")
            artifacts_raw = await asyncio.to_thread(_list_artifacts, client, project_name, short_name)
            artifacts = sorted((_shape_artifact(a) for a in artifacts_raw), key=lambda a: a["push_time"] or "")
            repo_bytes = sum(a["size_bytes"] for a in artifacts)
            total_bytes += repo_bytes
            repo_reports.append(
                {
                    "repository": short_name,
                    "artifact_count": len(artifacts),
                    "pull_count": int(repo.get("pull_count", 0) or 0),
                    "size": size_human(repo_bytes),
                    "size_bytes": repo_bytes,
                    "artifacts": artifacts,
                }
            )

        repo_reports.sort(key=lambda r: r["size_bytes"], reverse=True)
        await _report(ctx, 1.0, "report complete")

        result: StorageReportOutput = {
            "project": project_name,
            "total_repositories": len(repos),
            "total_size": size_human(total_bytes),
            "total_size_bytes": total_bytes,
            "repositories": repo_reports,
        }
        header = (
            f"## Storage report for {project_name}\n\n"
            f"Total: **{size_human(total_bytes)}** across {len(repos)} repos\n\n"
        )
        md = header + "\n".join(
            [f"- **{r['repository']}** — {r['size']} ({r['artifact_count']} artifacts)" for r in repo_reports]
        )
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"building storage report for {project_name}")


@mcp.tool(
    name="harbor_cleanup_candidates",
    annotations={
        "title": "Cleanup Candidates",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
async def harbor_cleanup_candidates(
    project_name: Annotated[str, Field(min_length=1, max_length=255, description="Harbor project name.")],
    ctx: Context,
    include_untagged: Annotated[
        bool, Field(default=True, description="Suggest deleting untagged artifacts (orphaned layers).")
    ] = True,
    include_zero_pulls: Annotated[
        bool, Field(default=True, description="Suggest deleting artifacts that have never been pulled.")
    ] = True,
    keep_latest_per_repo: Annotated[
        int, Field(default=1, ge=0, le=100, description="How many newest artifacts to always keep per repository.")
    ] = 1,
) -> CleanupCandidatesOutput:
    """Suggest which artifacts could be deleted to reclaim space.

    **READ-ONLY** — never deletes anything; just produces a list with
    reasons. Use ``harbor_delete_artifact`` / ``harbor_delete_untagged`` /
    ``harbor_delete_old_artifacts`` to act on the results.

    Reasons emitted:
        - ``untagged``      — artifact has no tags (orphaned layer)
        - ``never_pulled``  — artifact has never been pulled (and is past
          the ``keep_latest_per_repo`` cutoff)
        - ``old_version``   — artifact is older than the ``keep_latest_per_repo``
          newest tagged artifacts
    """
    try:
        client = get_client()
        await _report(ctx, 0.05, f"listing repositories in {project_name}")
        repos = await asyncio.to_thread(_list_repos, client, project_name)
        candidates: list[CleanupCandidate] = []
        total_reclaimable = 0
        for i, repo in enumerate(repos):
            short_name = repo["name"].replace(f"{project_name}/", "")
            await _report(ctx, 0.1 + 0.85 * (i / max(len(repos), 1)), f"scanning {short_name}")
            artifacts_raw = await asyncio.to_thread(_list_artifacts, client, project_name, short_name)
            sorted_arts = sorted(artifacts_raw, key=lambda a: a.get("push_time") or "", reverse=True)

            for idx, art in enumerate(sorted_arts):
                shaped = _shape_artifact(art)
                reasons: list[str] = []
                is_untagged = not shaped["tags"]
                no_pulls = shaped["pull_time"] is None

                if include_untagged and is_untagged:
                    reasons.append("untagged")
                if idx >= keep_latest_per_repo and include_zero_pulls and no_pulls:
                    reasons.append("never_pulled")
                if idx >= keep_latest_per_repo and not is_untagged and len(sorted_arts) > keep_latest_per_repo:
                    reasons.append("old_version")

                if reasons:
                    total_reclaimable += shaped["size_bytes"]
                    candidates.append(
                        {
                            "repository": short_name,
                            "tags": shaped["tags"] or [],
                            "digest": shaped["digest"],
                            "size": shaped["size"],
                            "size_bytes": shaped["size_bytes"],
                            "push_time": shaped["push_time"],
                            "reasons": reasons,
                        }
                    )

        candidates.sort(key=lambda c: c["size_bytes"], reverse=True)
        await _report(ctx, 1.0, f"{len(candidates)} candidates found")

        result: CleanupCandidatesOutput = {
            "project": project_name,
            "candidates_count": len(candidates),
            "total_reclaimable": size_human(total_reclaimable),
            "total_reclaimable_bytes": total_reclaimable,
            "candidates": candidates,
            "hint": (
                "Use harbor_delete_artifact for individual artifacts, "
                "harbor_delete_untagged for bulk untagged, or "
                "harbor_delete_old_artifacts (dry_run=True) for keep-N policies."
            ),
        }
        header = (
            f"## Cleanup candidates in {project_name}\n\n"
            f"Reclaimable: **{size_human(total_reclaimable)}** "
            f"across {len(candidates)} artifacts\n\n"
        )
        md = header + "\n".join(
            [
                f"- **{c['repository']}** — "
                f"{','.join(c['tags']) or '(untagged)'} — "
                f"{c['size']} ({', '.join(c['reasons'])})"
                for c in candidates[:30]
            ]
        )
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"finding cleanup candidates in {project_name}")


# ── Destructive tools ──────────────────────────────────────────────────────


@mcp.tool(
    name="harbor_delete_artifact",
    annotations={
        "title": "Delete Single Artifact",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
    structured_output=True,
)
def harbor_delete_artifact(
    project_name: Annotated[str, Field(min_length=1, max_length=255, description="Harbor project name.")],
    repository_name: Annotated[
        str, Field(min_length=1, max_length=255, description="Repository name within the project.")
    ],
    reference: Annotated[
        str, Field(min_length=1, max_length=255, description="Tag (e.g. 'v1.0') or digest (e.g. 'sha256:...').")
    ],
) -> DeleteArtifactOutput:
    """Delete a single artifact by tag or digest.

    **DESTRUCTIVE & IRREVERSIBLE** — Harbor immediately removes the manifest
    from its catalogue; the underlying blobs are reclaimed by the next GC
    sweep. There is no soft-delete or undo.

    Returns the freed space and tag list for confirmation.
    """
    try:
        client = get_client()
        # Resolve the artifact first so we can return helpful metadata.
        artifacts = _list_artifacts(client, project_name, repository_name)
        target = None
        for art in artifacts:
            tags = [t["name"] for t in (art.get("tags") or [])]
            if art.get("digest") == reference or reference in tags:
                target = art
                break

        if target is None:
            empty: DeleteArtifactOutput = {
                "success": False,
                "project": project_name,
                "repository": repository_name,
                "reference": reference,
                "deleted_tags": [],
                "freed_size": "0 B",
                "freed_bytes": 0,
                "error": f"Artifact {reference!r} not found in {project_name}/{repository_name}",
            }
            md = f"❌ Artifact {reference!r} not found in {project_name}/{repository_name}"
            return output.ok(empty, md)  # type: ignore[return-value]

        client.delete(
            f"/projects/{project_name}/repositories/{encode_repo(repository_name)}/artifacts/{encode_repo(reference)}"
        )

        size = int(target.get("size", 0) or 0)
        tags = [t["name"] for t in (target.get("tags") or [])]
        result: DeleteArtifactOutput = {
            "success": True,
            "project": project_name,
            "repository": repository_name,
            "reference": reference,
            "deleted_tags": tags,
            "freed_size": size_human(size),
            "freed_bytes": size,
            "error": None,
        }
        md = (
            f"✅ Deleted {project_name}/{repository_name}@{reference} "
            f"(tags: {','.join(tags) or '(untagged)'}, freed {size_human(size)})"
        )
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"deleting artifact {reference} in {project_name}/{repository_name}")


@mcp.tool(
    name="harbor_delete_untagged",
    annotations={
        "title": "Delete Untagged Artifacts",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
async def harbor_delete_untagged(
    project_name: Annotated[str, Field(min_length=1, max_length=255, description="Harbor project name.")],
    ctx: Context,
    repository_name: Annotated[
        str | None,
        Field(
            default=None,
            description="If set, only that repository is processed; otherwise every repository in the project.",
        ),
    ] = None,
) -> DeleteUntaggedOutput:
    """Delete all untagged artifacts in a project (or single repository).

    **DESTRUCTIVE.** Untagged artifacts are typically orphaned layers left
    behind after pushing a new tag of the same image — generally safe to
    delete. The full project sweep is opaque, so the response includes
    ``repos_scanned`` for visibility.
    """
    try:
        client = get_client()
        if repository_name:
            repos_to_clean = [repository_name]
        else:
            await _report(ctx, 0.05, f"enumerating repositories in {project_name}")
            repos = await asyncio.to_thread(_list_repos, client, project_name)
            repos_to_clean = [r["name"].replace(f"{project_name}/", "") for r in repos]

        deleted: list[DeletedArtifact] = []
        errors: list[DeleteErrorItem] = []
        total_freed = 0

        for i, repo in enumerate(repos_to_clean):
            await _report(ctx, 0.1 + 0.85 * (i / max(len(repos_to_clean), 1)), f"sweeping {repo}")
            artifacts = await asyncio.to_thread(_list_artifacts, client, project_name, repo)
            for art in artifacts:
                tags = [t["name"] for t in (art.get("tags") or [])]
                if tags:
                    continue
                digest = art.get("digest", "")
                size = int(art.get("size", 0) or 0)
                try:
                    client.delete(
                        f"/projects/{project_name}/repositories/{encode_repo(repo)}/artifacts/{encode_repo(digest)}"
                    )
                    total_freed += size
                    deleted.append({"repository": repo, "digest": digest[:19], "size": size_human(size)})
                except Exception as e:
                    errors.append({"repository": repo, "digest": digest[:19], "error": str(e)[:120]})

        await _report(ctx, 1.0, f"{len(deleted)} deleted, {len(errors)} failed")

        result: DeleteUntaggedOutput = {
            "project": project_name,
            "repos_scanned": repos_to_clean,
            "deleted_count": len(deleted),
            "freed_size": size_human(total_freed),
            "freed_bytes": total_freed,
            "deleted": deleted,
            "errors": errors,
        }
        md = (
            f"## Untagged sweep in {project_name}\n\n"
            f"Deleted **{len(deleted)}** artifacts "
            f"({size_human(total_freed)} freed) "
            f"across {len(repos_to_clean)} repos."
        )
        if errors:
            md += f"\n\n⚠ {len(errors)} errors — see ``errors`` field."
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"deleting untagged artifacts in {project_name}")


@mcp.tool(
    name="harbor_delete_old_artifacts",
    annotations={
        "title": "Delete Old Artifacts (keep N)",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
    structured_output=True,
)
def harbor_delete_old_artifacts(
    project_name: Annotated[str, Field(min_length=1, max_length=255, description="Harbor project name.")],
    repository_name: Annotated[
        str, Field(min_length=1, max_length=255, description="Repository name within the project.")
    ],
    keep_count: Annotated[int, Field(default=1, ge=0, le=1000, description="Number of newest artifacts to keep.")] = 1,
    dry_run: Annotated[
        bool, Field(default=True, description="If True (default) — only report what would be deleted, do not delete.")
    ] = True,
) -> DeleteOldOutput:
    """Keep the N newest artifacts in a repository, delete the rest.

    **DESTRUCTIVE.** ``dry_run=True`` is the default — the agent must
    explicitly set ``dry_run=False`` to actually delete. Each entry in
    ``to_delete`` carries a ``deleted`` field (``True``/``False`` after
    real run, ``None`` in dry-run).
    """
    try:
        client = get_client()
        artifacts = _list_artifacts(client, project_name, repository_name)
        sorted_arts = sorted(artifacts, key=lambda a: a.get("push_time") or "", reverse=True)
        to_keep_raw = sorted_arts[:keep_count]
        to_delete_raw = sorted_arts[keep_count:]

        if not to_delete_raw:
            empty: DeleteOldOutput = {
                "project": project_name,
                "repository": repository_name,
                "dry_run": dry_run,
                "keeping": [
                    {
                        "tags": [t["name"] for t in (a.get("tags") or [])],
                        "digest": a.get("digest", "")[:19],
                        "push_time": _short(a.get("push_time")),
                    }
                    for a in to_keep_raw
                ],
                "to_delete_count": 0,
                "freed_size": "0 B",
                "freed_bytes": 0,
                "to_delete": [],
                "hint": None,
                "message": f"Nothing to delete — repository has {len(sorted_arts)} artifacts, keep_count={keep_count}.",
            }
            return output.ok(empty, empty["message"] or "")  # type: ignore[return-value]

        to_delete: list[ToDeleteArtifact] = []
        kept: list[KeptArtifact] = [
            {
                "tags": [t["name"] for t in (a.get("tags") or [])],
                "digest": a.get("digest", "")[:19],
                "push_time": _short(a.get("push_time")),
            }
            for a in to_keep_raw
        ]
        total_freed = 0

        for art in to_delete_raw:
            digest = art.get("digest", "")
            tags = [t["name"] for t in (art.get("tags") or [])]
            size = int(art.get("size", 0) or 0)
            entry: ToDeleteArtifact = {
                "tags": tags,
                "digest": digest[:19],
                "size": size_human(size),
                "push_time": _short(art.get("push_time")),
                "deleted": None,
            }
            if not dry_run:
                try:
                    client.delete(
                        f"/projects/{project_name}/repositories/{encode_repo(repository_name)}/artifacts/{encode_repo(digest)}"
                    )
                    entry["deleted"] = True
                    total_freed += size
                except Exception as e:
                    entry["deleted"] = False
                    entry["tags"].append(f"(error: {str(e)[:80]})")
            else:
                total_freed += size
            to_delete.append(entry)

        result: DeleteOldOutput = {
            "project": project_name,
            "repository": repository_name,
            "dry_run": dry_run,
            "keeping": kept,
            "to_delete_count": len(to_delete),
            "freed_size": size_human(total_freed),
            "freed_bytes": total_freed,
            "to_delete": to_delete,
            "hint": "Re-run with dry_run=False to perform the deletion." if dry_run else None,
            "message": None,
        }
        prefix = "🔎 dry-run" if dry_run else "🗑 deleted"
        md = (
            f"## {prefix}: keep {keep_count} of {len(sorted_arts)} "
            f"in {project_name}/{repository_name} "
            f"({size_human(total_freed)} freed)"
        )
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"deleting old artifacts in {project_name}/{repository_name}")

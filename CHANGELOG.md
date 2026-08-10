# Changelog

All notable changes to `harbor-registry-mcp` are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use [SemVer](https://semver.org/).

## [0.1.1] — 2026-08-10

### Fixed
- Pin `mcp>=1.2,<2`. `mcp` 2.0 dropped `mcp.server.fastmcp`, so a clean install
  (`pip install harbor-registry-mcp`) resolved to 2.0 and the server failed at import:
  `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. The console script
  `harbor-registry-mcp` did not start at all, which surfaced in MCP clients as an
  opaque transport error.

## [0.1.0] — 2026-04-18

### Added
- Initial release with 8 tools covering Harbor 2.x REST API:
  - `harbor_list_projects` — projects with repo counts
  - `harbor_list_repos` — repositories in a project
  - `harbor_list_artifacts` — artifacts with tags, size, scan status
  - `harbor_storage_report` — full project storage breakdown
  - `harbor_cleanup_candidates` — suggest artifacts to delete (untagged / never-pulled / old)
  - `harbor_delete_artifact` — delete single artifact (destructive)
  - `harbor_delete_untagged` — delete all untagged in project/repo (destructive)
  - `harbor_delete_old_artifacts` — keep N latest, delete rest (dry-run default, destructive)
- FastMCP + Pydantic input validation + TypedDict output schemas.
- Structured error mapping (401 / 403 / 404 / 429 / 5xx).
- Tool annotations: `readOnlyHint` / `destructiveHint` / `idempotentHint`.
- `HARBOR_SSL_VERIFY` toggle for self-signed certificates.
- MIT license.
- Published on PyPI and in the MCP Registry as `io.github.mshegolev/harbor-registry-mcp`.

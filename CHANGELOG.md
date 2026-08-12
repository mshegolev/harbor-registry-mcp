# Changelog

All notable changes to `harbor-registry-mcp` are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use [SemVer](https://semver.org/).

## [Unreleased]

### Changed
- Every tool parameter now declares its default exactly once, in the function
  signature. Twelve parameters previously wrote it twice — `Annotated[bool,
  Field(default=True, …)] = True` — and only the signature one ever reached the
  `inputSchema` an MCP client reads; pydantic discards the `Field` default when a
  signature default is present. Editing `Field(default=…)` therefore changed
  nothing on the wire while looking decisive in review. No default value changed:
  the emitted `inputSchema`/`outputSchema` are byte-identical to 0.1.1.

### Added
- `test_input_schema_defaults` pins every default the client is told about (and
  the absence of one on required parameters); `test_every_dry_run_flag_defaults_to_true`
  holds the bulk-delete guard for any tool exposing `dry_run`, not just today's;
  `test_defaults_are_declared_once` fails if the second, ineffective default
  creeps back into a `Field(...)`.

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

# Changelog

All notable changes to `harbor-registry-mcp` are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use [SemVer](https://semver.org/).

## [Unreleased]

## [0.2.0] — 2026-08-13

### Changed
- **`harbor_delete_untagged` no longer deletes by default.** It now takes
  `dry_run: bool = True`, matching its milder neighbour
  `harbor_delete_old_artifacts`. Protection was distributed backwards: the
  keep-N tool, which touches one repository and spares the N newest artifacts,
  was guarded — while the untagged sweep, which walks **every** repository in
  the project when `repository_name` is omitted and whose own docstring admits
  "the full project sweep is opaque", had no dry-run at all. In dry-run **no
  delete request is issued**; the tool only collects candidates.

  This is a behaviour change at the call site: a call that used to delete now
  reports. Pass `dry_run=False` to get the old behaviour.
- Every tool parameter now declares its default exactly once, in the function
  signature. Twelve parameters previously wrote it twice — `Annotated[bool,
  Field(default=True, …)] = True` — and only the signature one ever reached the
  `inputSchema` an MCP client reads; pydantic discards the `Field` default when a
  signature default is present. Editing `Field(default=…)` therefore changed
  nothing on the wire while looking decisive in review. No default value changed
  in that refactor: it left the emitted schemas byte-identical to 0.1.1.

### Added
- `DeleteUntaggedOutput` gained `dry_run` and `hint`; each entry in `deleted`
  gained `deleted` (`True` after a real delete, `None` in dry-run) — the same
  way `harbor_delete_old_artifacts` marks "what would go". `deleted_count` and
  `freed_*` therefore describe candidates in dry-run, as they do there.
- Tests: the dry-run guard is asserted against a stub client that records every
  `delete()` call, so "dry-run deletes nothing" is proven by an empty call log
  rather than by the response text. `test_bulk_delete_defaults_to_dry_run` is
  now parametrized over both bulk-delete tools and fails if either one *loses*
  its `dry_run` — a hole `test_every_dry_run_flag_defaults_to_true` cannot
  cover, since it only judges the flags that still exist.
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

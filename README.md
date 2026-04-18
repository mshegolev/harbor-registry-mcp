# harbor-registry-mcp

<!-- mcp-name: io.github.mshegolev/harbor-registry-mcp -->

[![PyPI](https://img.shields.io/pypi/v/harbor-registry-mcp.svg?logo=pypi&logoColor=white)](https://pypi.org/project/harbor-registry-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/harbor-registry-mcp.svg?logo=python&logoColor=white)](https://pypi.org/project/harbor-registry-mcp/)
[![License: MIT](https://img.shields.io/pypi/l/harbor-registry-mcp.svg)](LICENSE)

MCP server for [Harbor Registry](https://goharbor.io/). Lets an LLM agent (Claude Code, Cursor, OpenCode, etc.) list projects, repositories and artifacts, run **storage reports**, find **cleanup candidates**, and delete untagged or old artifacts — all with safety rails (dry-run by default for bulk delete).

Python, [FastMCP](https://github.com/modelcontextprotocol/python-sdk), stdio transport.

Works with any Harbor 2.x instance — SaaS or self-hosted / on-prem.

## Why another Harbor MCP?

A couple of community Harbor MCPs exist ([`nomagicln/mcp-harbor`](https://github.com/nomagicln/mcp-harbor), [`bupd/harbor-mcp-server`](https://github.com/bupd/harbor-mcp-server)) but they expose only the basic list/get endpoints. This one adds **storage reports, cleanup candidates, delete untagged, and delete old artifacts with dry-run** — the operations DevOps engineers actually need to reclaim disk space.

## Design highlights

- **Tool annotations** — read-only tools get `readOnlyHint: True`; destructive ones (`harbor_delete_*`) carry `destructiveHint: True` so MCP clients ask for confirmation.
- **Dry-run by default** on bulk cleanup (`harbor_delete_old_artifacts(dry_run=True)`) — the agent must flip it to execute.
- **Structured output** — every tool returns a typed payload (TypedDict) + a markdown summary.
- **Structured errors** — 401 / 403 / 404 / 429 / 5xx mapped to actionable hints.
- **Pydantic input validation** for every argument.
- **Vulnerability snapshot** — `harbor_list_artifacts` surfaces scan status and counts if `with_scan_overview` is enabled.

## Features (8 tools)

**Discovery & inspection**
- `harbor_list_projects` — projects with repo counts and visibility
- `harbor_list_repos` — repositories in a project
- `harbor_list_artifacts` — artifacts in a repository with tags/size/scan status
- `harbor_storage_report` — full project storage breakdown (all repos × all artifacts)

**Cleanup planning**
- `harbor_cleanup_candidates` — suggest what to delete (untagged, never-pulled, old versions)

**Cleanup execution (destructive)**
- `harbor_delete_artifact` — delete a single artifact by tag or digest
- `harbor_delete_untagged` — delete all untagged artifacts in a project/repo
- `harbor_delete_old_artifacts` — keep N latest per repo, delete the rest (dry-run default)

## Installation

Requires Python 3.10+.

```bash
# via uvx (recommended)
uvx --from harbor-registry-mcp harbor-registry-mcp

# or via pipx
pipx install harbor-registry-mcp
```

## Configuration

```bash
claude mcp add harbor -s project \
  --env HARBOR_URL=https://harbor.example.com \
  --env HARBOR_USERNAME='robot$your-robot' \
  --env HARBOR_PASSWORD=your-robot-token \
  --env HARBOR_SSL_VERIFY=true \
  -- uvx --from harbor-registry-mcp harbor-registry-mcp
```

Or in `.mcp.json`:

```json
{
  "mcpServers": {
    "harbor": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "harbor-registry-mcp", "harbor-registry-mcp"],
      "env": {
        "HARBOR_URL": "https://harbor.example.com",
        "HARBOR_USERNAME": "robot$your-robot",
        "HARBOR_PASSWORD": "${HARBOR_PASSWORD}",
        "HARBOR_SSL_VERIFY": "true"
      }
    }
  }
}
```

Check:

```bash
claude mcp list
# harbor: uvx --from harbor-registry-mcp harbor-registry-mcp - ✓ Connected
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `HARBOR_URL` | yes | Harbor URL (no trailing slash) |
| `HARBOR_USERNAME` | yes | Harbor username — robot account recommended |
| `HARBOR_PASSWORD` | yes | Password or robot token |
| `HARBOR_SSL_VERIFY` | no | `true`/`false`. Default: `true`. |

## Example usage

- "Storage report for project `einvy-pub`"
- "Find cleanup candidates in `qa-assistant` — keep latest 3"
- "Delete all untagged artifacts in `qa-assistant`"
- "Dry-run delete of old artifacts in `qa-assistant/pgvector-rag`, keep 1 latest"
- "What's in `einvy-pub/my-image`?"

## Safety

- Read tools use `readOnlyHint: True` — no confirmation needed.
- Delete tools use `destructiveHint: True` — clients should confirm.
- `harbor_delete_old_artifacts` defaults to `dry_run=True`; the agent must explicitly set `dry_run=False` to actually delete.
- `harbor_cleanup_candidates` is **read-only** — it only suggests candidates, never deletes.

## Development

```bash
git clone https://github.com/mshegolev/harbor-registry-mcp.git
cd harbor-registry-mcp
pip install -e '.[dev]'
pytest
```

## License

MIT © Mikhail Shchegolev

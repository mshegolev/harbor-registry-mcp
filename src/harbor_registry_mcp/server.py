"""FastMCP server entry point for Harbor Registry MCP."""

from __future__ import annotations

# Importing the tools module attaches @mcp.tool decorators to the shared
# FastMCP instance.
from harbor_registry_mcp import tools as _tools  # noqa: F401
from harbor_registry_mcp._mcp import app_lifespan, mcp


def main() -> None:
    """Entry point for the ``harbor-registry-mcp`` console script (stdio)."""
    mcp.run()


__all__ = ["mcp", "app_lifespan", "main"]


if __name__ == "__main__":
    main()

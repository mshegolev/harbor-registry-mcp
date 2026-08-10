"""Wire-protocol smoke-test of the MCP facade (substitute for MCP Inspector).

Every other test in this repo imports helpers or the HTTP client — none of
them touch the FastMCP facade, so a broken facade (e.g. ``mcp`` 2.0 dropping
``mcp.server.fastmcp``) would kill the ``harbor-registry-mcp`` console script
while the suite stayed green. This module closes that hole:

- imports the entry-point module named in ``[project.scripts]`` and resolves
  the installed console script through package metadata;
- builds the shared ``FastMCP`` instance and asks it for its tool catalogue
  (``mcp.list_tools()`` is the in-process equivalent of a ``tools/list``
  request);
- pins the catalogue — all 8 tools, their annotations, their input schemas
  and the generated ``outputSchema``.

No network, no credentials: ``list_tools`` is pure introspection.
"""

from __future__ import annotations

import asyncio
from importlib.metadata import entry_points
from typing import Any

import pytest

# Importing tools attaches @mcp.tool decorators.
import harbor_registry_mcp.tools  # noqa: F401
from harbor_registry_mcp._mcp import mcp

EXPECTED_TOOLS: dict[str, dict[str, Any]] = {
    "harbor_list_projects": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": set(),
        "optional_params": {"page", "page_size"},
    },
    "harbor_list_repos": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"project_name"},
        "optional_params": {"page", "page_size"},
    },
    "harbor_list_artifacts": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"project_name", "repository_name"},
        "optional_params": {"page", "page_size"},
    },
    "harbor_storage_report": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"project_name"},
        "optional_params": set(),
    },
    "harbor_cleanup_candidates": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"project_name"},
        "optional_params": {"include_untagged", "include_zero_pulls", "keep_latest_per_repo"},
    },
    "harbor_delete_artifact": {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "required_params": {"project_name", "repository_name", "reference"},
        "optional_params": set(),
    },
    "harbor_delete_untagged": {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "required_params": {"project_name"},
        "optional_params": {"repository_name"},
    },
    "harbor_delete_old_artifacts": {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "required_params": {"project_name", "repository_name"},
        "optional_params": {"keep_count", "dry_run"},
    },
}

DESTRUCTIVE_TOOLS = {"harbor_delete_artifact", "harbor_delete_untagged", "harbor_delete_old_artifacts"}


@pytest.fixture(scope="module")
def listed_tools() -> list[Any]:
    """One-shot handshake equivalent: fetch the tool catalogue FastMCP exposes."""
    return asyncio.run(mcp.list_tools())


def test_entry_point_module_imports_and_shares_the_server() -> None:
    """``harbor_registry_mcp.server`` is what the console script runs.

    It must import cleanly and expose the *same* FastMCP instance the tools
    registered themselves on — otherwise the launched server serves nothing.
    """
    from harbor_registry_mcp import _mcp as shared
    from harbor_registry_mcp import server

    assert callable(server.main)
    assert server.mcp is shared.mcp
    assert callable(getattr(server.mcp, "run", None)), "FastMCP instance has no run() — facade broken"


def test_console_script_target_resolves() -> None:
    """The ``[project.scripts]`` target must be loadable from installed metadata.

    This is the packaging-level equivalent of running the binary: if the
    module path in ``pyproject.toml`` drifts, or an import inside it explodes,
    ``ep.load()`` raises here instead of at a user's first launch.
    """
    scripts = [ep for ep in entry_points(group="console_scripts") if ep.name == "harbor-registry-mcp"]
    assert scripts, "console script 'harbor-registry-mcp' not registered — package not installed?"
    assert scripts[0].value == "harbor_registry_mcp.server:main"
    assert callable(scripts[0].load())


def test_all_eight_tools_registered(listed_tools: list[Any]) -> None:
    names = {t.name for t in listed_tools}
    assert len(listed_tools) == 8, f"expected 8 tools, got {len(listed_tools)}"
    assert names == set(EXPECTED_TOOLS), (
        f"tool list mismatch.\n  registered: {sorted(names)}\n  expected:   {sorted(EXPECTED_TOOLS)}"
    )


@pytest.mark.parametrize("tool_name", list(EXPECTED_TOOLS))
def test_tool_annotations(listed_tools: list[Any], tool_name: str) -> None:
    """Every tool must carry readOnly/destructive/idempotent hints matching our design."""
    tool = next(t for t in listed_tools if t.name == tool_name)
    ann = tool.annotations
    expected = EXPECTED_TOOLS[tool_name]
    assert ann.readOnlyHint is expected["readOnlyHint"], f"{tool_name}.readOnlyHint"
    assert ann.destructiveHint is expected["destructiveHint"], f"{tool_name}.destructiveHint"
    assert ann.idempotentHint is expected["idempotentHint"], f"{tool_name}.idempotentHint"


def test_only_delete_tools_are_destructive(listed_tools: list[Any]) -> None:
    """A read-only tool silently gaining destructiveHint (or losing it) is a
    safety regression an agent would act on."""
    destructive = {t.name for t in listed_tools if t.annotations.destructiveHint}
    assert destructive == DESTRUCTIVE_TOOLS


@pytest.mark.parametrize("tool_name", list(EXPECTED_TOOLS))
def test_input_schema_shape(listed_tools: list[Any], tool_name: str) -> None:
    """Required + optional parameter sets must match the tool signatures."""
    tool = next(t for t in listed_tools if t.name == tool_name)
    schema = tool.inputSchema
    assert schema["type"] == "object"
    properties = set(schema.get("properties", {}).keys())
    required = set(schema.get("required", []))

    expected = EXPECTED_TOOLS[tool_name]
    assert required == expected["required_params"], (
        f"{tool_name}.required: got {required}, expected {expected['required_params']}"
    )
    expected_all = expected["required_params"] | expected["optional_params"]
    assert expected_all.issubset(properties), f"{tool_name}: missing properties {expected_all - properties}"
    # ``ctx`` is injected by FastMCP and must never leak into the client-facing schema.
    assert "ctx" not in properties, f"{tool_name}: Context param leaked into inputSchema"


@pytest.mark.parametrize("tool_name", list(EXPECTED_TOOLS))
def test_output_schema_is_generated(listed_tools: list[Any], tool_name: str) -> None:
    """structured_output=True must produce an outputSchema for every tool."""
    tool = next(t for t in listed_tools if t.name == tool_name)
    assert tool.outputSchema is not None, f"{tool_name} has no outputSchema"
    assert tool.outputSchema.get("type") == "object", f"{tool_name} outputSchema not an object"
    assert tool.outputSchema.get("properties"), f"{tool_name} outputSchema has no properties"


def test_bulk_delete_defaults_to_dry_run(listed_tools: list[Any]) -> None:
    """``harbor_delete_old_artifacts`` is irreversible; the schema an agent
    reads must advertise ``dry_run`` defaulting to True."""
    tool = next(t for t in listed_tools if t.name == "harbor_delete_old_artifacts")
    dry_run = tool.inputSchema["properties"]["dry_run"]
    assert dry_run.get("default") is True, f"dry_run default is {dry_run.get('default')!r}, expected True"

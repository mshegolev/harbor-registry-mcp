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
  (including every advertised **default**) and the generated ``outputSchema``.

No network, no credentials: ``list_tools`` is pure introspection.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from importlib.metadata import entry_points
from typing import Any

import pytest

# Importing tools attaches @mcp.tool decorators.
import harbor_registry_mcp.tools
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
        "optional_params": {"repository_name", "dry_run"},
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

# Destructive tools that act on *many* artifacts in one call. Both must ship a
# dry_run guard; ``harbor_delete_artifact`` is excluded because it deletes one
# explicitly named reference — the confirmation is the argument itself.
BULK_DELETE_TOOLS = {"harbor_delete_untagged", "harbor_delete_old_artifacts"}

# Every default an MCP client actually reads out of ``inputSchema``, per tool.
# Parameters absent from a tool's mapping must advertise *no* default at all.
#
# Why this is pinned rather than obvious: a parameter written as
# ``Annotated[bool, Field(default=True, ...)] = True`` carries the default
# twice, and only the **signature** one survives into the schema — pydantic
# discards ``Field(default=…)`` when a signature default is present, silently.
# So editing ``Field(default=True)`` to ``False`` changes nothing on the wire
# while looking decisive in review. The repo therefore keeps exactly one
# default per parameter (in the signature); this table is the wire-level proof
# that it says what we think it says, and ``test_defaults_are_declared_once``
# below stops the second default from creeping back.
EXPECTED_SCHEMA_DEFAULTS: dict[str, dict[str, Any]] = {
    "harbor_list_projects": {"page": 1, "page_size": 50},
    "harbor_list_repos": {"page": 1, "page_size": 50},
    "harbor_list_artifacts": {"page": 1, "page_size": 50},
    "harbor_storage_report": {},
    "harbor_cleanup_candidates": {
        "include_untagged": True,
        "include_zero_pulls": True,
        "keep_latest_per_repo": 1,
    },
    "harbor_delete_artifact": {},
    "harbor_delete_untagged": {"repository_name": None, "dry_run": True},
    "harbor_delete_old_artifacts": {"keep_count": 1, "dry_run": True},
}


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


@pytest.mark.parametrize("tool_name", sorted(BULK_DELETE_TOOLS))
def test_bulk_delete_defaults_to_dry_run(listed_tools: list[Any], tool_name: str) -> None:
    """Both bulk deletes are irreversible; the schema an agent reads must
    advertise ``dry_run`` defaulting to True.

    Named per tool on purpose: ``test_every_dry_run_flag_defaults_to_true``
    below only judges the flags that exist, so a tool *losing* its ``dry_run``
    would slip past it. This one fails if the flag is gone.
    """
    tool = next(t for t in listed_tools if t.name == tool_name)
    properties = tool.inputSchema["properties"]
    assert "dry_run" in properties, f"{tool_name} no longer exposes dry_run — bulk-delete guard removed"
    dry_run = properties["dry_run"]
    assert dry_run.get("default") is True, f"{tool_name}.dry_run default is {dry_run.get('default')!r}, expected True"


def test_every_dry_run_flag_defaults_to_true(listed_tools: list[Any]) -> None:
    """Whatever the catalogue grows into, a ``dry_run`` switch always starts safe.

    Broader than the test above on purpose: a *new* destructive tool that ships
    ``dry_run=False`` would slip past a check pinned to one tool name.
    """
    flags = {
        t.name: t.inputSchema["properties"]["dry_run"].get("default")
        for t in listed_tools
        if "dry_run" in t.inputSchema.get("properties", {})
    }
    assert flags, "no tool exposes dry_run any more — the bulk-delete guard disappeared"
    unsafe = {name: value for name, value in flags.items() if value is not True}
    assert not unsafe, f"dry_run must default to True; got {unsafe}"


@pytest.mark.parametrize("tool_name", list(EXPECTED_TOOLS))
def test_input_schema_defaults(listed_tools: list[Any], tool_name: str) -> None:
    """Pin every default the client is told about — and the absence of the rest.

    Equality (not ``issubset``) is deliberate: it fails both when a default
    changes value and when a required parameter silently acquires one, which
    would turn a mandatory argument into an optional guess.
    """
    tool = next(t for t in listed_tools if t.name == tool_name)
    advertised = {
        name: spec["default"] for name, spec in tool.inputSchema.get("properties", {}).items() if "default" in spec
    }
    assert advertised == EXPECTED_SCHEMA_DEFAULTS[tool_name], (
        f"{tool_name}: schema defaults drifted.\n"
        f"  advertised: {advertised}\n"
        f"  expected:   {EXPECTED_SCHEMA_DEFAULTS[tool_name]}"
    )


def _annotated_field_defaults(tree: ast.Module) -> list[str]:
    """Find parameters declaring a default *both* in ``Field(...)`` and in the signature."""
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        args = node.args
        positional = args.posonlyargs + args.args
        # ``args.defaults`` covers only the *trailing* positional params — left-pad it.
        padded: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
        paired: list[tuple[ast.arg, ast.expr | None]] = list(zip(positional, padded, strict=True))
        paired += list(zip(args.kwonlyargs, args.kw_defaults, strict=True))

        for arg, signature_default in paired:
            annotation = arg.annotation
            if signature_default is None or not isinstance(annotation, ast.Subscript):
                continue
            if getattr(annotation.value, "id", None) != "Annotated":
                continue
            metadata = annotation.slice.elts[1:] if isinstance(annotation.slice, ast.Tuple) else []
            for item in metadata:
                if not isinstance(item, ast.Call):
                    continue
                name = getattr(item.func, "id", None) or getattr(item.func, "attr", None)
                if name != "Field":
                    continue
                for keyword in item.keywords:
                    if keyword.arg in {"default", "default_factory"}:
                        offenders.append(f"{node.name}.{arg.arg} (line {arg.lineno}): Field({keyword.arg}=…)")
    return offenders


def test_defaults_are_declared_once() -> None:
    """A parameter may state its default in exactly one place — the signature.

    ``Annotated[bool, Field(default=True, ...)] = True`` compiles, runs and lies:
    pydantic keeps the signature default and drops the ``Field`` one, so the two
    can drift apart and the schema silently follows the one nobody reads first.
    On ``dry_run``, guarding an irreversible bulk delete, that is a safety bug
    dressed as a style nit.
    """
    package_dir = pathlib.Path(harbor_registry_mcp.tools.__file__).parent
    modules = sorted(package_dir.glob("*.py"))
    assert modules, f"no sources found under {package_dir} — the scan would pass vacuously"

    offenders: list[str] = []
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        offenders += [f"{module.name}: {hit}" for hit in _annotated_field_defaults(tree)]
    assert not offenders, "default declared twice (Field + signature); drop the Field one:\n  " + "\n  ".join(offenders)

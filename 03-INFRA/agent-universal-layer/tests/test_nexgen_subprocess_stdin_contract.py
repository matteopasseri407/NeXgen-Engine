"""A process the gateway can start never inherits the gateway's own stdin.

`lazy-mcp.py` serves tool calls on worker threads while its main thread blocks reading the
protocol stream from stdin. A helper process started without an explicit stdin inherits that
pipe: on Windows it then waits behind the pending read (the v2.4.0 provisioning hang), and an
installer that prompts would eat protocol bytes. So every launch in a module the gateway
imports, directly or through another module, declares its stdin (normally DEVNULL).

The reachable set is computed from the gateway's own imports, so a new module it starts
importing is covered without anyone editing this file.
"""
from __future__ import annotations

import ast
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
GATEWAY = Path(__file__).resolve().parents[1] / "mcp" / "lazy-mcp.py"
LAUNCHERS = {"run", "Popen", "check_output", "call", "check_call"}


def _module_file(name: str) -> Path | None:
    base = SCRIPTS_DIR.joinpath(*name.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def _engine_imports(path: Path, package: str) -> set[str]:
    """Every `nexgen_core` module a file names, wherever in the file the import sits."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)] if node.level > 1 else anchor
                module = ".".join(anchor + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            names = [module] + [f"{module}.{alias.name}" for alias in node.names]
        else:
            continue
        found.update(name for name in names if name.split(".")[0] == "nexgen_core")
    return found


def _reachable_from_gateway() -> dict[str, Path]:
    todo = sorted(_engine_imports(GATEWAY, ""))
    reached: dict[str, Path] = {}
    while todo:
        name = todo.pop()
        path = _module_file(name)
        if name in reached or path is None:
            continue
        reached[name] = path
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        todo.extend(_engine_imports(path, package))
    return reached


def _launches_without_stdin(path: Path) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value
        if not (isinstance(owner, ast.Name) and owner.id == "subprocess" and node.func.attr in LAUNCHERS):
            continue
        declared = {keyword.arg for keyword in node.keywords}
        # `**options` may carry it; the call site cannot be judged from here, so it is trusted.
        if not declared & {"stdin", "input", None}:
            lines.append(node.lineno)
    return lines


def test_the_gateway_reaches_the_modules_that_start_processes():
    reached = _reachable_from_gateway()
    assert {"nexgen_core.provision", "nexgen_core.processes", "nexgen_core.skill_sources"} <= set(reached)


def test_every_launch_the_gateway_can_reach_declares_its_stdin():
    offenders = [
        f"{path.relative_to(SCRIPTS_DIR)}:{line}"
        for path in _reachable_from_gateway().values()
        for line in _launches_without_stdin(path)
    ]
    assert not offenders, (
        "a helper started from the gateway inherits the protocol stream; pass stdin=subprocess.DEVNULL: "
        + ", ".join(sorted(offenders))
    )

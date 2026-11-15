"""A stand-in for `openclaw config patch`, for the tests that drive a real file.

The rules are OpenClaw's own, as its `--help` states them: objects merge
recursively, arrays and scalars replace, `--replace-path` makes that subtree
exactly what the patch says, and null deletes a path. The previous bytes are kept
as `<config>.bak`, which is the rotating backup a real patch leaves behind. A dry
run answers in the JSON shape the CLI prints, so the gate that reads it is
exercised by the same fake as the write.

A test that drives `config_write` through this sees the file a real run would
leave, without starting OpenClaw. Nothing in the engine calls this module.
"""
from __future__ import annotations

import json
from pathlib import Path

from nexgen_core.openclaw_service import RunResult


def answer_patch(argv: list[str], env: dict[str, str], *, errors: list[str] = ()) -> RunResult:
    """Play `openclaw config patch` for one call: a dry run judges, a write applies.

    `errors` is what a refusing OpenClaw would say: the dry run reports them as
    typed JSON errors, and the write declines without touching the file.
    """
    path = Path(env["OPENCLAW_CONFIG_PATH"])
    patch = json.loads(Path(argv[argv.index("--file") + 1]).read_text(encoding="utf-8"))
    replace_paths = [argv[index + 1] for index, arg in enumerate(argv) if arg == "--replace-path"]
    if "--dry-run" in argv:
        if errors:
            typed = [{"kind": "schema", "message": message} for message in errors]
            return RunResult(1, json.dumps({"ok": False, "errors": typed}), "")
        return RunResult(0, json.dumps({"ok": True, "operations": len(patch)}), "")
    if errors:
        return RunResult(1, "", "Config change declined. No settings were saved.\n" + "\n".join(errors))
    apply_patch(path, patch, replace_paths)
    return RunResult(0, "", "")


def apply_patch(path: Path, patch: dict, replace_paths: list[str]) -> None:
    """Apply one patch to the file the way OpenClaw would, backup included."""
    before = path.read_text(encoding="utf-8") if path.is_file() else None
    if before is not None:
        path.with_name(path.name + ".bak").write_text(before, encoding="utf-8")
    document = json.loads(before) if before else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_merged(document, patch, replace_paths), indent=2) + "\n", encoding="utf-8")


def _merged(document: dict, patch: dict, replace_paths: list[str]) -> dict:
    """The merge itself: an object merges unless its path is one to replace."""
    merged = dict(document)
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        elif key in replace_paths or not isinstance(value, dict) or not isinstance(merged.get(key), dict):
            merged[key] = value
        else:
            merged[key] = _merged(merged[key], value, [])
    return merged

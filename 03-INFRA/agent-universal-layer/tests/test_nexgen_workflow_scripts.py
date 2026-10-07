"""Every script the workflows run is run the way a runner runs it: as a plain `python3 path.py`, nothing installed.

The unit tests import the engine as a library with `pythonpath` set, which is exactly the setting a runner does not
have. An import placed before a script's own `sys.path` shim passes every test and then breaks the CI step, or the
release step after the tag is already signed. This runs each script named in `.github/workflows/*.yml` from an empty
directory with a clean environment and refuses an import error.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"
#: `python3 03-INFRA/....py` as a workflow writes it, possibly after `$(`, `|| ` or an environment assignment.
INVOCATION = re.compile(r"python3?\s+((?:\./)?03-INFRA/[\w./-]+\.py)")


def invoked_scripts() -> list[str]:
    found = set()
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        found.update(INVOCATION.findall(workflow.read_text(encoding="utf-8")))
    return sorted(path.removeprefix("./") for path in found)


def test_the_workflows_name_scripts_at_all():
    assert len(invoked_scripts()) >= 6, "the pattern no longer finds the workflows' scripts"


@pytest.mark.parametrize("script", invoked_scripts())
def test_a_workflow_script_starts_with_nothing_installed(script, tmp_path):
    path = REPO / script
    assert path.is_file(), f"a workflow runs {script}, which is not in the repository"
    neutral = subprocess.run([sys.executable, "-c", "import nexgen_core"], cwd=tmp_path, capture_output=True, text=True)
    if neutral.returncode == 0:
        pytest.skip("nexgen_core is installed in this interpreter, which would hide the very import error this looks for")
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "LANG": "C"}
    proc = subprocess.run([sys.executable, str(path), "--help"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert "ModuleNotFoundError" not in proc.stderr and "ImportError" not in proc.stderr, proc.stderr[-600:]

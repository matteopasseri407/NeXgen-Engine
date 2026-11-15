"""The manual compaction the Antigravity plugin does not declare, and the setup that adds it.

`agy` 1.3.5 added `/compact`; release 0.3.4 of the `google-antigravity-cli`
plugin predates it and declares no `manualCompaction`, so OpenClaw refuses to
compact a session on that backend. The engine inserts the missing declaration
into the installed plugin, once, beside a backup.

One promise per test:

- a plugin that is not installed is reported as missing and nothing is written;
- the declaration lands once, inside the backend object, and the old file is saved;
- a second run finds its own marker and writes nothing;
- a file whose shape changed is left alone and named;
- a dry run prints the plan and writes nothing;
- the groom puts the declaration back when a plugin update removed it, and only then;
- the setup says one line per outcome and stays quiet when the patch is already there.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import agy_compaction, plugin_patch  # noqa: E402
from nexgen_core.openclaw.setup_agy import _compaction_line  # noqa: E402

#: The two lines of a compiled backend this suite needs: the anchor, and a sibling.
BACKEND_SOURCE = """\
export function buildBackend(id) {
    const backend = {
        id,
        ownsNativeCompaction: true,
        bundleMcp: true,
    };
    return backend;
}
"""


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    """English output to assert on, whatever the machine's own language is."""
    i18n.set_language("en")


def _installed(tmp_path: Path, source: str = BACKEND_SOURCE) -> Path:
    """A state folder holding an installed plugin with the given compiled backend."""
    path = agy_compaction.backend_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_a_plugin_that_is_not_installed_is_missing_and_writes_nothing(tmp_path: Path) -> None:
    result = agy_compaction.apply(tmp_path)

    assert result.state == plugin_patch.MISSING
    assert not agy_compaction.backend_file(tmp_path).exists()


def test_the_declaration_lands_once_and_the_old_file_is_saved(tmp_path: Path) -> None:
    path = _installed(tmp_path)

    result = agy_compaction.apply(tmp_path)

    assert result.state == plugin_patch.PATCHED
    assert result.backup is not None and result.backup.exists()
    assert result.backup.read_text(encoding="utf-8") == BACKEND_SOURCE
    updated = path.read_text(encoding="utf-8")
    assert updated.count("manualCompaction:") == 1
    assert updated.count("nexgen-manual-compaction") == 1
    assert '"arg"' in updated
    assert '"/compact"' in updated
    # The anchor and its neighbours survive: the patch is additive.
    assert "ownsNativeCompaction: true," in updated
    assert "bundleMcp: true," in updated


def test_a_second_run_finds_its_marker_and_writes_nothing(tmp_path: Path) -> None:
    path = _installed(tmp_path)
    agy_compaction.apply(tmp_path)
    after_first = path.read_text(encoding="utf-8")

    result = agy_compaction.apply(tmp_path)

    assert result.state == plugin_patch.ALREADY
    assert result.backup is None
    assert path.read_text(encoding="utf-8") == after_first


def test_a_file_without_the_expected_shape_is_left_alone(tmp_path: Path) -> None:
    path = _installed(tmp_path, "export const backend = { ownsNativeCompaction: false };\n")
    before = path.read_text(encoding="utf-8")

    result = agy_compaction.apply(tmp_path)

    assert result.state == plugin_patch.UNEXPECTED
    assert path.read_text(encoding="utf-8") == before


def test_a_dry_run_plans_and_writes_nothing(tmp_path: Path) -> None:
    path = _installed(tmp_path)
    before = path.read_text(encoding="utf-8")

    result = agy_compaction.apply(tmp_path, dry_run=True)

    assert result.state == plugin_patch.WOULD_PATCH
    assert path.read_text(encoding="utf-8") == before
    assert result.backup is None


def test_reapply_puts_the_declaration_back_after_a_plugin_update(tmp_path: Path) -> None:
    """The update rewrites the file without the marker, and the hourly groom must fix it itself."""
    path = _installed(tmp_path)

    assert agy_compaction.reapply(tmp_path) is True
    assert agy_compaction.MARKER in path.read_text(encoding="utf-8")
    # The second call has nothing to do: a patched plugin is never rewritten.
    assert agy_compaction.reapply(tmp_path) is False


def test_reapply_writes_nothing_when_the_plugin_is_absent(tmp_path: Path) -> None:
    assert agy_compaction.reapply(tmp_path) is False
    assert not agy_compaction.backend_file(tmp_path).exists()


def test_the_setup_says_one_line_per_outcome(tmp_path: Path) -> None:
    path = _installed(tmp_path)

    assert "now carries OpenClaw's manual compaction" in (_compaction_line(agy_compaction.apply(tmp_path)) or "")
    # Already patched: nothing to say, so the setup stays quiet on every later run.
    assert _compaction_line(agy_compaction.apply(tmp_path)) is None
    unexpected = plugin_patch.Patch(plugin_patch.UNEXPECTED, path)
    assert "no longer has the shape" in (_compaction_line(unexpected) or "")
    failed = plugin_patch.Patch(plugin_patch.FAILED, path, detail="permission denied")
    assert "permission denied" in (_compaction_line(failed) or "")
    planned = plugin_patch.Patch(plugin_patch.WOULD_PATCH, path)
    assert "nothing is written" in (_compaction_line(planned) or "")

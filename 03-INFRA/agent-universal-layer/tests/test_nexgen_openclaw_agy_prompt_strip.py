"""The paragraph break the Antigravity plugin's prompt filter deletes, and the setup that restores it.

Release 0.3.4 of the `google-antigravity-cli` plugin removes OpenClaw's
`⟦openclaw:ctx⟧` context blocks before `agy` sees a turn. When it removes a
block it also eats the blank line in front of it, so the text that followed the
block lands on the closing fence of the block before. A Telegram turn carries a
"Conversation info" block and then a "Conversation context" block: the second
removal glues the user's message to the first block's closing ```, and the
first removal then deletes the message with it. `agy` receives only the runtime
instructions and answers something else.

One promise per test:

- the plugin as released drops the message, and the patched filter keeps it while
  still removing both context blocks;
- a plugin that is not installed is reported as missing and nothing is written;
- the fix lands once, beside a backup, and a second run writes nothing;
- a file whose shape changed is left alone and named;
- a dry run prints the plan and writes nothing;
- the setup says one line per outcome and stays quiet when the fix is already there.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import agy_compaction, agy_prompt_strip  # noqa: E402
from nexgen_core.openclaw.setup_agy import _prompt_strip_line  # noqa: E402

NODE = shutil.which("node")
#: The filter exactly as plugin 0.3.4 ships it (MIT, jason-allen-oneal/openclaw-google-antigravity-provider).
RELEASED = Path(__file__).resolve().parent / "fixtures" / "agy-plugin" / "prompt-strip.js"
MESSAGE = "this is a test message"

#: A Telegram turn shaped the way OpenClaw 2026.11 composes it, with invented content.
PROMPT = "\n".join([
    "Conversation info: ⟦openclaw:ctx⟧",
    "```json",
    '{"chat_id":"telegram:1","message_id":"7","timestamp":"Thu 2026-11-12 17:38:59 UTC"}',
    "```",
    "",
    "Conversation context (chronological, selected for current message): ⟦openclaw:ctx⟧",
    "#session:a 2026-11-12 12:38:22 UTC User: an earlier question",
    "#session:b 2026-11-12 12:38:46 UTC OpenClaw: an earlier answer",
    "",
    MESSAGE,
    "",
    "Use `message` with `final=false` for progress.",
])


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    """English output to assert on, whatever the machine's own language is."""
    i18n.set_language("en")


def _installed(tmp_path: Path, source: str | None = None) -> Path:
    """A state folder holding an installed plugin with the given prompt filter."""
    path = agy_prompt_strip.filter_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(RELEASED.read_text(encoding="utf-8") if source is None else source, encoding="utf-8")
    return path


def _strip(filter_path: Path) -> str:
    """What the plugin hands `agy` for PROMPT, computed by the filter file itself."""
    module = filter_path.with_suffix(".mjs")
    shutil.copyfile(filter_path, module)
    script = (
        f"const m = await import({json.dumps(module.resolve().as_uri())});"
        f"process.stdout.write(JSON.stringify(m.stripOpenClawChannelContext({json.dumps(PROMPT)})));"
    )
    done = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_released_filter_drops_the_message_and_the_patched_one_keeps_it(tmp_path: Path) -> None:
    path = _installed(tmp_path)
    released = _strip(path)
    assert MESSAGE not in released  # the bug, as plugin 0.3.4 ships it

    assert agy_prompt_strip.apply(tmp_path).state == agy_compaction.PATCHED
    fixed = _strip(path)

    assert fixed.startswith(MESSAGE)
    assert "⟦openclaw:ctx⟧" not in fixed
    assert "an earlier answer" not in fixed
    assert fixed.endswith("Use `message` with `final=false` for progress.")


def test_a_plugin_that_is_not_installed_is_missing_and_writes_nothing(tmp_path: Path) -> None:
    result = agy_prompt_strip.apply(tmp_path)

    assert result.state == agy_compaction.MISSING
    assert not agy_prompt_strip.filter_file(tmp_path).exists()


def test_the_fix_lands_once_and_the_old_file_is_saved(tmp_path: Path) -> None:
    path = _installed(tmp_path)
    before = path.read_text(encoding="utf-8")

    first = agy_prompt_strip.apply(tmp_path)
    after_first = path.read_text(encoding="utf-8")
    second = agy_prompt_strip.apply(tmp_path)

    assert first.state == agy_compaction.PATCHED
    assert first.backup is not None and first.backup.read_text(encoding="utf-8") == before
    assert after_first.count(agy_prompt_strip.MARKER) == 1
    assert agy_prompt_strip.ANCHOR not in after_first
    assert second.state == agy_compaction.ALREADY and second.backup is None
    assert path.read_text(encoding="utf-8") == after_first


def test_a_file_without_the_expected_shape_is_left_alone(tmp_path: Path) -> None:
    path = _installed(tmp_path, "export function stripOpenClawChannelContext(p) { return p; }\n")
    before = path.read_text(encoding="utf-8")

    result = agy_prompt_strip.apply(tmp_path)

    assert result.state == agy_compaction.UNEXPECTED
    assert path.read_text(encoding="utf-8") == before


def test_a_dry_run_plans_and_writes_nothing(tmp_path: Path) -> None:
    path = _installed(tmp_path)
    before = path.read_text(encoding="utf-8")

    result = agy_prompt_strip.apply(tmp_path, dry_run=True)

    assert result.state == agy_compaction.WOULD_PATCH
    assert result.backup is None
    assert path.read_text(encoding="utf-8") == before


def test_the_setup_says_one_line_per_outcome(tmp_path: Path) -> None:
    path = _installed(tmp_path)

    assert "keeps the user's message" in (_prompt_strip_line(agy_prompt_strip.apply(tmp_path)) or "")
    assert _prompt_strip_line(agy_prompt_strip.apply(tmp_path)) is None
    unexpected = agy_compaction.Patch(agy_compaction.UNEXPECTED, path)
    assert "no longer has the shape" in (_prompt_strip_line(unexpected) or "")
    failed = agy_compaction.Patch(agy_compaction.FAILED, path, detail="permission denied")
    assert "permission denied" in (_prompt_strip_line(failed) or "")
    planned = agy_compaction.Patch(agy_compaction.WOULD_PATCH, path)
    assert "nothing is written" in (_prompt_strip_line(planned) or "")

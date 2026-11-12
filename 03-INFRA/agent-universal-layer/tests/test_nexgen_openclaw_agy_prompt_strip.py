"""The Antigravity plugin's prompt filter, fixed so it never deletes the user's message.

Release 0.3.4 of the `google-antigravity-cli` plugin removes OpenClaw's
`⟦openclaw:ctx⟧` context blocks before `agy` sees a turn. It had two faults:

- it ate the blank line in front of each block it cut, so the text after a block
  landed on the closing fence of the block before and the next cut deleted it.
  On a Telegram turn that text is the user's message;
- it decided where a block ends by counting code fences, so one stray fence made
  it delete everything up to the end of the prompt.

OpenClaw itself splits these blocks on blank lines (`split(/\\n{2,}/)`), collapses
every history line to one line and neutralises fences inside it. The fixed
filter uses the same rule: a block ends at its first blank line, and a ```json
block ends at its closing fence.

One promise per test:

- the plugin as released drops the message in the shapes below, the fixed filter keeps it;
- prompts the released filter already handled come out exactly the same;
- a plugin that is not installed is reported as missing and nothing is written;
- the fix lands once, beside a backup, a second run writes nothing, and a file
  carrying only the earlier one-line fix is completed;
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
from nexgen_core.openclaw import agy_prompt_strip, plugin_patch  # noqa: E402
from nexgen_core.openclaw.setup_agy import _prompt_strip_line  # noqa: E402

NODE = shutil.which("node")
#: The filter exactly as plugin 0.3.4 ships it (MIT, jason-allen-oneal/openclaw-google-antigravity-provider).
RELEASED = Path(__file__).resolve().parent / "fixtures" / "agy-plugin" / "prompt-strip.js"
#: The line the first version of this fix changed, before and after.
SEPARATOR_OLD = "            result = result.slice(0, sliceStart) + result.slice(blockEnd);\n"
SEPARATOR_V1 = (
    "            /* nexgen-prompt-strip-separator: keep a paragraph break where a context block was cut */\n"
    '            result = result.slice(0, sliceStart) + (sliceStart > 0 ? "\\n\\n" : "") + result.slice(blockEnd);\n'
)

MESSAGE = "this is a test message"
INSTRUCTIONS = "Use `message` with `final=false` for progress."
INFO = "\n".join([
    "Conversation info: ⟦openclaw:ctx⟧",
    "```json",
    '{"chat_id":"telegram:1","message_id":"7","timestamp":"Thu 2026-11-12 17:38:59 UTC"}',
    "```",
])
CONTEXT = "\n".join([
    "Conversation context (chronological, selected for current message): ⟦openclaw:ctx⟧",
    "#a 2026-11-12 12:38:22 UTC User: an earlier question",
    "#b 2026-11-12 12:38:46 UTC OpenClaw: an earlier answer",
])
GOAL = "Active goal: ship the release"

#: Shapes the released filter gets wrong: (name, prompt, what `agy` must receive).
BROKEN = [
    ("telegram-turn", f"{INFO}\n\n{CONTEXT}\n\n{MESSAGE}\n\n{INSTRUCTIONS}", f"{MESSAGE}\n\n{INSTRUCTIONS}"),
    ("stray-fence-in-history",
     f"{INFO}\n\n{CONTEXT}\n```stray\n\n{MESSAGE}\n\n{INSTRUCTIONS}", f"{MESSAGE}\n\n{INSTRUCTIONS}"),
    ("message-glued-to-json", f"{INFO}\n{MESSAGE}\n\n{INSTRUCTIONS}", f"{MESSAGE}\n\n{INSTRUCTIONS}"),
    ("user-code-block",
     f"{INFO}\n\n{CONTEXT}\n\nlook at this:\n```py\nx = 1\n\ny = 2\n```\n\n{INSTRUCTIONS}",
     f"look at this:\n```py\nx = 1\n\ny = 2\n```\n\n{INSTRUCTIONS}"),
    # A JSON block without its closing fence must not reach for a later fence in the user's text.
    ("json-without-closing-fence",
     'Conversation info: ⟦openclaw:ctx⟧\n```json\n{"a":1}\n\nplease fix:\n```js\nx()\n```\n\nthanks',
     "please fix:\n```js\nx()\n```\n\nthanks"),
]
#: Shapes the released filter already handles, which must not change.
UNCHANGED = [
    ("no-marker", f"plain text\n\n```\ncode\n```\n\n\n\n{MESSAGE}"),
    ("info-only", f"{INFO}\n\n{MESSAGE}"),
    ("context-only", f"{CONTEXT}\n\n{MESSAGE}"),
    ("block-last", f"{MESSAGE}\n\n{INFO}"),
    ("goal-before-message", f"{INFO}\n\n{GOAL}\n\n{MESSAGE}"),
    # A fence line that is not exactly the closing fence does not close the JSON block.
    ("json-closed-by-a-language-fence", f'Conversation info: ⟦openclaw:ctx⟧\n```json\n{{"a":1}}\n```text\n\n{MESSAGE}'),
]


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


def _strip(filter_path: Path, prompts: list[str]) -> list[str]:
    """What the given filter file hands `agy` for each prompt, computed by node."""
    module = filter_path.with_name(f"run-{filter_path.stat().st_mtime_ns}.mjs")
    shutil.copyfile(filter_path, module)
    script = (
        f"const m = await import({json.dumps(module.resolve().as_uri())});"
        f"process.stdout.write(JSON.stringify({json.dumps(prompts)}.map(m.stripOpenClawChannelContext)));"
    )
    done = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_released_filter_loses_the_message_and_the_fixed_one_keeps_it(tmp_path: Path) -> None:
    path = _installed(tmp_path)
    prompts = [prompt for _, prompt, _ in BROKEN]
    released = _strip(path, prompts)

    assert agy_prompt_strip.apply(tmp_path).state == plugin_patch.PATCHED
    fixed = _strip(path, prompts)

    for (name, _, expected), before, after in zip(BROKEN, released, fixed):
        assert before != expected, f"{name}: the released filter already handles this"
        assert after == expected, f"{name}: {after!r}"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_prompts_the_released_filter_handled_come_out_the_same(tmp_path: Path) -> None:
    path = _installed(tmp_path)
    prompts = [prompt for _, prompt in UNCHANGED]
    released = _strip(path, prompts)

    agy_prompt_strip.apply(tmp_path)

    assert _strip(path, prompts) == released


def test_a_plugin_that_is_not_installed_is_missing_and_writes_nothing(tmp_path: Path) -> None:
    result = agy_prompt_strip.apply(tmp_path)

    assert result.state == plugin_patch.MISSING
    assert not agy_prompt_strip.filter_file(tmp_path).exists()


def test_the_fix_lands_once_and_the_old_file_is_saved(tmp_path: Path) -> None:
    path = _installed(tmp_path)
    before = path.read_text(encoding="utf-8")

    first = agy_prompt_strip.apply(tmp_path)
    after_first = path.read_text(encoding="utf-8")
    second = agy_prompt_strip.apply(tmp_path)

    assert first.state == plugin_patch.PATCHED
    assert first.backup is not None and first.backup.read_text(encoding="utf-8") == before
    assert after_first.count(agy_prompt_strip.MARKER) == 1
    assert "inCodeFence" not in after_first
    assert second.state == plugin_patch.ALREADY and second.backup is None
    assert path.read_text(encoding="utf-8") == after_first


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_file_with_only_the_earlier_one_line_fix_is_completed(tmp_path: Path) -> None:
    released = RELEASED.read_text(encoding="utf-8")
    path = _installed(tmp_path, released.replace(SEPARATOR_OLD, SEPARATOR_V1, 1))

    result = agy_prompt_strip.apply(tmp_path)

    assert result.state == plugin_patch.PATCHED
    text = path.read_text(encoding="utf-8")
    assert text.count("nexgen-prompt-strip-separator") == 1
    assert agy_prompt_strip.MARKER in text
    assert _strip(path, [BROKEN[1][1]]) == [BROKEN[1][2]]


def test_a_file_without_the_expected_shape_is_left_alone(tmp_path: Path) -> None:
    path = _installed(tmp_path, "export function stripOpenClawChannelContext(p) { return p; }\n")
    before = path.read_text(encoding="utf-8")

    result = agy_prompt_strip.apply(tmp_path)

    assert result.state == plugin_patch.UNEXPECTED
    assert path.read_text(encoding="utf-8") == before


def test_a_dry_run_plans_and_writes_nothing(tmp_path: Path) -> None:
    path = _installed(tmp_path)
    before = path.read_text(encoding="utf-8")

    result = agy_prompt_strip.apply(tmp_path, dry_run=True)

    assert result.state == plugin_patch.WOULD_PATCH
    assert result.backup is None
    assert path.read_text(encoding="utf-8") == before


def test_the_setup_says_one_line_per_outcome(tmp_path: Path) -> None:
    path = _installed(tmp_path)

    assert "keeps the user's message" in (_prompt_strip_line(agy_prompt_strip.apply(tmp_path)) or "")
    assert _prompt_strip_line(agy_prompt_strip.apply(tmp_path)) is None
    unexpected = plugin_patch.Patch(plugin_patch.UNEXPECTED, path)
    assert "no longer has the shape" in (_prompt_strip_line(unexpected) or "")
    failed = plugin_patch.Patch(plugin_patch.FAILED, path, detail="permission denied")
    assert "permission denied" in (_prompt_strip_line(failed) or "")
    planned = plugin_patch.Patch(plugin_patch.WOULD_PATCH, path)
    assert "nothing is written" in (_prompt_strip_line(planned) or "")

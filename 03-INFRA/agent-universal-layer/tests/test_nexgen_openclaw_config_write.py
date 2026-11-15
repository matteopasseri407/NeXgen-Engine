"""The patch document handed to `openclaw config patch`, and the answer it gives back.

Two promises are tested here. The patch carries the gateway token, so it is
private from the first byte and removed once it has been read. And OpenClaw's
answer is read as a verdict: `--dry-run --json` is what gates the write, and the
sentence it prints instead of JSON, when the file in force cannot be parsed, is
still a refusal a person can act on.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw import config_write  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw_service import RunResult  # noqa: E402


def test_the_patch_file_is_private_to_its_owner(tmp_path: Path):
    """It carries the gateway token, so it is created at 0600, never chmod'ed after."""
    path = tmp_path / "openclaw.json"

    written = config_write.write_patch(path, '{"gateway": {}}\n')

    assert written == path.with_name("openclaw.json.patch.json5")
    assert written.read_text(encoding="utf-8") == '{"gateway": {}}\n'
    if sys.platform != "win32":
        assert written.stat().st_mode & 0o777 == 0o600


def test_the_patch_file_is_removed_after_the_write(tmp_path: Path):
    path = tmp_path / "openclaw.json"
    config_write.write_patch(path, "{}\n")

    config_write.clear_patch(path)

    assert not path.with_name("openclaw.json.patch.json5").exists()
    config_write.clear_patch(path), "removing it twice is not an error"


def test_the_backup_is_the_one_openclaw_keeps():
    """The step that says where the previous file went names OpenClaw's own copy."""
    assert config_write.backup_path(Path("/home/user/.openclaw/openclaw.json")).name == "openclaw.json.bak"


def test_an_accepted_dry_run_reads_as_a_verdict():
    result = RunResult(0, '{"ok": true, "operations": 3, "checks": {"schema": true}}', "")

    verdict = config_write.verdict(result)

    assert verdict.ok is True
    assert verdict.detail == ""


def test_a_refused_dry_run_carries_the_reason():
    """Every typed error the CLI reports has a sentence a person can act on."""
    result = RunResult(1, (
        '{"ok": false, "operations": 2, "errors": ['
        '{"kind": "model", "message": "Unknown model: openai/gpt-5"},'
        '{"kind": "schema", "message": "gateway.port: expected number"}'
        "]}"
    ), "")

    verdict = config_write.verdict(result)

    assert verdict.ok is False
    assert verdict.detail == "Unknown model: openai/gpt-5\ngateway.port: expected number"


def test_a_file_that_cannot_be_parsed_is_refused_in_its_own_words():
    """OpenClaw prints the reason instead of JSON, and the person still reads it."""
    result = RunResult(1, "", (
        "OpenClaw config is invalid: ~/.openclaw/openclaw.json\n"
        "- <root>: JSON5 parse failed: SyntaxError: JSON5: invalid end of input at 3:1\n"
        "Run `openclaw doctor --fix` to repair, then retry."
    ))

    verdict = config_write.verdict(result)

    assert verdict.ok is False
    assert "JSON5 parse failed" in verdict.detail
    assert "openclaw doctor --fix" in verdict.detail


@pytest.mark.parametrize("stdout", ["", "not json at all", "[1, 2]"])
def test_an_answer_that_is_not_a_json_object_is_a_refusal(stdout):
    """A stream the CLI did not fill is not consent: nothing it says means "ok"."""
    verdict = config_write.verdict(RunResult(1, stdout, ""))

    assert verdict.ok is False

"""Regression test — per-CLI enforcement surface of `_build_seat_command`.

A prior review confirmed the Council's "consulenti senza mani" exception
(see `test_agents_md_council_exception.py`) is a textual instruction that
every seat prompt carries, but asked whether the *invoking CLI* also makes
tool/MCP calls impossible at the process level, or only Claude does.

Verified 2026-07-12 against the real installed binaries on the dev machine
(`--help` output, plus live invocations inspected via each CLI's own JSONL
session logs where `--help` alone was ambiguous — see the long comment
above `_build_seat_command` in `council.py` for the full per-CLI evidence):

- `claude` (`--tools ""`) and `ollama` (no `--experimental` flag passed) are
  the only two seats verified to have NO invocable tool surface by
  construction, independent of the prompt.
- `codex` (`-s read-only`) and `agy` (`--sandbox`) are both documented by
  the vendor's own help/product text to scope the shell/terminal-command
  tool only, not MCP servers. No CLI flag was found that closes that gap
  without risking a broken seat, so nothing was added for them.
- `opencode` has no CLI-level tool/MCP block at all today; confirmed no
  flag exists for it either.

These tests pin the *current, verified-safe* argv shape for every seat so a
future edit cannot silently drop a real enforcement flag (`--tools ""`,
`-s read-only`, `--sandbox`) or silently add a capability-widening one
(`--dangerously-skip-permissions`, `--auto`, `--experimental*`) without at
least breaking a test that says so.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

COUNCIL_PATH = Path(__file__).resolve().parents[1] / "council" / "council.py"


def load_council(monkeypatch, tmp_path):
    vault = tmp_path / "KnowledgeVault"
    monkeypatch.setenv("KNOWLEDGE_VAULT_PATH", str(vault))
    module_name = f"council_seat_flags_under_test_{id(tmp_path)}"
    spec = importlib.util.spec_from_file_location(module_name, COUNCIL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    mod.SESSIONS_DIR = tmp_path / "sessions"
    mod.SEATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    return mod


def test_claude_seat_keeps_the_comprehensive_tool_block(monkeypatch, tmp_path):
    council = load_council(monkeypatch, tmp_path)
    invocation = council._build_seat_command({"cli": "claude", "model": "vendor/test"}, "prompt", tmp_path)

    argv = invocation.argv
    assert "--permission-mode" in argv and argv[argv.index("--permission-mode") + 1] == "plan"
    assert "--tools" in argv and argv[argv.index("--tools") + 1] == ""
    assert "--no-session-persistence" in argv
    assert "--dangerously-skip-permissions" not in argv


def test_codex_seat_keeps_the_read_only_sandbox(monkeypatch, tmp_path):
    council = load_council(monkeypatch, tmp_path)
    invocation = council._build_seat_command({"cli": "codex", "model": "vendor/test"}, "prompt", tmp_path)

    argv = invocation.argv
    assert "-s" in argv and argv[argv.index("-s") + 1] == "read-only"
    # Known residual gap (documented, not fixed): -s only scopes the shell/
    # exec tool, not MCP servers. No flag exists yet to change that, so we
    # only guard against ever loosening the sandbox further by accident.
    for unsafe in ("-s workspace-write", "danger-full-access", "--dangerously-bypass-approvals-and-sandbox"):
        assert unsafe not in " ".join(argv)


def test_agy_seat_keeps_sandbox_and_never_adds_bypass(monkeypatch, tmp_path):
    council = load_council(monkeypatch, tmp_path)
    invocation = council._build_seat_command({"cli": "agy", "model": "vendor/test"}, "prompt", tmp_path)

    argv = invocation.argv
    assert "--sandbox" in argv
    assert "--dangerously-skip-permissions" not in argv
    assert "--auto" not in argv


def test_opencode_seat_never_gains_an_auto_approve_flag(monkeypatch, tmp_path):
    # opencode has no CLI-level MCP block today (documented gap, see the
    # comment above _build_seat_command). This test does not assert a
    # positive enforcement flag that does not exist; it only guards against
    # the seat accidentally gaining a capability-widening flag later.
    council = load_council(monkeypatch, tmp_path)
    invocation = council._build_seat_command({"cli": "opencode", "model": "vendor/test"}, "prompt", tmp_path)

    argv = invocation.argv
    assert "--auto" not in argv
    assert "--dangerously-skip-permissions" not in argv
    assert "--pure" not in argv  # --pure blocks plugins, a different subsystem; do not conflate it with an MCP block


def test_ollama_seat_never_passes_the_experimental_tool_loop_flags(monkeypatch, tmp_path):
    """`ollama run <model>` gains a tool-calling/agent loop ONLY behind the
    undocumented-by-default `--experimental` flag (confirmed via
    `ollama run --help`, version 0.30.10 client, 2026-07-12). The Council
    seat never passes it, which is the one place where "no tools" is
    verified safe by construction for a CLI other than claude. Pin that."""
    council = load_council(monkeypatch, tmp_path)
    invocation = council._build_seat_command({"cli": "ollama", "model": "vendor/test"}, "prompt", tmp_path)

    argv = invocation.argv
    assert argv == ["ollama", "run", "vendor/test"]
    for flag in ("--experimental", "--experimental-websearch", "--experimental-yolo"):
        assert flag not in argv


# ── reasoning_effort forwarding (beta-readiness review, 2026-07-13) ───────
# routing-status showed reasoning_effort as active for every seat regardless
# of whether the underlying CLI branch actually read it. claude/codex always
# did (--effort / model_reasoning_effort); ollama/opencode did not, silently.

def test_ollama_seat_forwards_a_recognized_reasoning_effort(monkeypatch, tmp_path):
    """--think <low|medium|high> is ollama's real reasoning-effort control
    (verified via `ollama run --help`), the same concept as claude's
    --effort. Only forwarded for the values ollama documents."""
    council = load_council(monkeypatch, tmp_path)
    invocation = council._build_seat_command(
        {"cli": "ollama", "model": "vendor/test", "reasoning_effort": "high"}, "prompt", tmp_path
    )

    argv = invocation.argv
    assert "--think" in argv and argv[argv.index("--think") + 1] == "high"


def test_ollama_seat_drops_an_unrecognized_reasoning_effort(monkeypatch, tmp_path):
    """A value ollama doesn't document (e.g. 'xhigh', a valid claude/codex
    tier) must never reach --think as a literal -- ollama would reject an
    unknown value outright, failing the whole seat over a flag it can't
    honor anyway."""
    council = load_council(monkeypatch, tmp_path)
    invocation = council._build_seat_command(
        {"cli": "ollama", "model": "vendor/test", "reasoning_effort": "xhigh"}, "prompt", tmp_path
    )

    assert "--think" not in invocation.argv


def test_opencode_seat_forwards_reasoning_effort_as_variant(monkeypatch, tmp_path):
    """--variant is opencode's real reasoning-effort control (verified via
    `opencode run --help`: "model variant (provider-specific reasoning
    effort, e.g., high, max, minimal)"), forwarded as-is since opencode
    documents it as provider-specific with no fixed enum to validate here."""
    council = load_council(monkeypatch, tmp_path)
    invocation = council._build_seat_command(
        {"cli": "opencode", "model": "vendor/test", "reasoning_effort": "max"}, "prompt", tmp_path
    )

    argv = invocation.argv
    assert "--variant" in argv and argv[argv.index("--variant") + 1] == "max"


def test_opencode_seat_omits_variant_when_effort_is_none_or_unset(monkeypatch, tmp_path):
    council = load_council(monkeypatch, tmp_path)
    for seat in (
        {"cli": "opencode", "model": "vendor/test"},
        {"cli": "opencode", "model": "vendor/test", "reasoning_effort": "none"},
    ):
        invocation = council._build_seat_command(seat, "prompt", tmp_path)
        assert "--variant" not in invocation.argv

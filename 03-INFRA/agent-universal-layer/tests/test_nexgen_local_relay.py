"""Test del relay F4: isolamento, flag di sola lettura, estrazione, tetti.

Le CLI sono finte: script POSIX in una cartella temporanea messa su PATH.
Nessuna quota reale viene spesa e nessun test dipende dalle CLI installate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.relay import MAX_OUTPUT, RelayError, available_clis, run_relay

pytestmark = pytest.mark.skipif(os.name != "posix", reason="fake CLI are POSIX scripts")

CLAUDE_FAKE = """DIR=$(dirname "$0")
printf '%s\\n' "$@" > "$DIR/args.txt"
cat > "$DIR/stdin.txt"
printf '{"result":"risposta finta claude"}\\n'
"""

CODEX_FAKE = """DIR=$(dirname "$0")
printf '%s\\n' "$@" > "$DIR/args.txt"
printf '%s\\n' "${CODEX_HOME:-}" > "$DIR/codex_home.txt"
if [ -f "${CODEX_HOME:-}/auth.json" ]; then echo yes > "$DIR/auth_copied.txt"; else echo no > "$DIR/auth_copied.txt"; fi
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  prev="$a"
done
cat > "$DIR/stdin.txt"
if [ -n "$out" ]; then printf 'risposta finta codex\\n' > "$out"; fi
printf 'banner\\n'
"""

OPENCODE_FAKE = """DIR=$(dirname "$0")
printf '%s\\n' "$@" > "$DIR/args.txt"
att=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--file" ]; then att="$a"; fi
  prev="$a"
done
if [ -n "$att" ]; then cp "$att" "$DIR/attached.txt"; fi
printf '\\033[0m> build · finto\\nrisposta finta opencode\\n'
"""


def _fake_bin(tmp_path: Path, name: str, body: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text("#!/usr/bin/env sh\n" + body, encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def _cfg(tmp_path: Path) -> LaneConfig:
    return LaneConfig(
        vault_root=tmp_path / "vault",
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
    )


def _audit_lines(cfg: LaneConfig) -> list[dict]:
    return [json.loads(line) for line in cfg.audit_path.read_text(encoding="utf-8").strip().splitlines()]


def test_claude_relay_parses_json_and_keeps_tools_uninvocable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = _fake_bin(tmp_path, "claude", CLAUDE_FAKE, monkeypatch)
    cfg = _cfg(tmp_path)
    result = run_relay(cfg, "claude", "claude-opus-5", "Qual e' la capitale dell'Umbria?")
    assert result.answer == "risposta finta claude"
    args = (bin_dir / "args.txt").read_text(encoding="utf-8")
    assert "--tools" in args
    assert "--permission-mode" in args and "plan" in args
    assert "--model" in args and "claude-opus-5" in args
    stdin = (bin_dir / "stdin.txt").read_text(encoding="utf-8")
    assert "Qual e' la capitale dell'Umbria?" in stdin
    assert "sandbox di sola lettura" in stdin
    assert _audit_lines(cfg)[0]["tool"] == "relay"


def test_codex_relay_isolates_home_and_uses_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = _fake_bin(tmp_path, "codex", CODEX_FAKE, monkeypatch)
    cfg = _cfg(tmp_path)
    result = run_relay(cfg, "codex", "gpt-6-astra", "Riassumi in una riga.")
    assert result.answer == "risposta finta codex"
    args = (bin_dir / "args.txt").read_text(encoding="utf-8")
    assert "-s" in args and "read-only" in args
    isolated = (bin_dir / "codex_home.txt").read_text(encoding="utf-8").strip()
    real_home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    assert isolated and isolated != real_home
    assert Path(isolated).name == "codex-home"
    real_auth = Path(real_home) / "auth.json"
    copied = (bin_dir / "auth_copied.txt").read_text(encoding="utf-8").strip()
    assert copied == ("yes" if real_auth.is_file() else "no")


def test_opencode_relay_strips_progress_and_attaches_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = _fake_bin(tmp_path, "opencode", OPENCODE_FAKE, monkeypatch)
    cfg = _cfg(tmp_path)
    result = run_relay(cfg, "opencode", "opencode/muse-spark-1.3-contributor-free", "Domanda di prova.")
    assert result.answer == "risposta finta opencode"
    attached = (bin_dir / "attached.txt").read_text(encoding="utf-8")
    assert "Domanda di prova." in attached
    assert "sandbox di sola lettura" in attached


def test_relay_refuses_unknown_cli(tmp_path: Path) -> None:
    with pytest.raises(RelayError, match="non supportato"):
        run_relay(_cfg(tmp_path), "agy", "model", "domanda")


def test_relay_refuses_missing_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "vuoto"))
    with pytest.raises(RelayError, match="non installato"):
        run_relay(_cfg(tmp_path), "claude", "model", "domanda")


def test_relay_caps_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = CODEX_FAKE.replace(
        "if [ -n \"$out\" ]; then printf 'risposta finta codex\\n' > \"$out\"; fi",
        "if [ -n \"$out\" ]; then head -c 30000 /dev/zero | tr '\\0' 'x' > \"$out\"; fi",
    )
    _fake_bin(tmp_path, "codex", fake, monkeypatch)
    result = run_relay(_cfg(tmp_path), "codex", "model", "domanda")
    assert result.truncated is True
    assert len(result.answer) <= MAX_OUTPUT + len("\n[...troncato]")


def test_relay_truncates_attachment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = _fake_bin(tmp_path, "opencode", OPENCODE_FAKE, monkeypatch)
    attach = tmp_path / "grande.txt"
    attach.write_text("x" * 70_000, encoding="utf-8")
    result = run_relay(_cfg(tmp_path), "opencode", "model", "domanda", attach=str(attach))
    assert result.truncated is True
    attached = (bin_dir / "attached.txt").read_text(encoding="utf-8")
    assert "[...allegato troncato]" in attached


def test_available_clis_lists_only_present_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = _fake_bin(tmp_path, "claude", CLAUDE_FAKE, monkeypatch)
    # PATH senza ~/.local/bin: le CLI vere installate sulla macchina non contano.
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    assert available_clis() == ["claude"]

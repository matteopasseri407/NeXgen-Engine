"""Pinning dello split env/argv/extract del relay prima del refactor.

Fissa il comportamento attuale (non quello desiderato):
- env isolato senza segreti per ogni CLI,
- argv contiene il comando atteso di ogni CLI,
- extract ritorna l'esito dall'output finto di ogni CLI,
- run_relay chiama i tre helper in sequenza,
- firme pubbliche invariate.

Solo CLI finti in tmp_path, mai CLI veri, mai rete. Nessun processo reale:
subprocess.run e' finto via monkeypatch, subprocess.Popen e' proibito e
ogni test gira con timeout corti.
"""

from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexgen_local import relay as relay_module
from nexgen_local.config import LaneConfig


@pytest.fixture(autouse=True)
def _no_real_processes(monkeypatch: pytest.MonkeyPatch):
    """Nessun processo reale od orfano: Popen e' proibito in questi test."""

    def _forbidden(*args, **kwargs):  # pragma: no cover - guardia
        raise AssertionError("processo reale proibito nei test pinnati")

    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    yield


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return LaneConfig(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
    )


@pytest.mark.parametrize("cli", ["claude", "codex", "opencode"])
def test_isolated_env_holds_no_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli: str
) -> None:
    monkeypatch.setenv("N8N_MCP_TOKEN", "synthetic-secret-aaa")
    monkeypatch.setenv("VAULT_LIBRARY_TOKEN", "synthetic-secret-bbb")
    monkeypatch.setenv("SYNTH_TEST_MCP_TOKEN", "synthetic-secret-ccc")
    workdir = tmp_path / f"wd-{cli}"
    workdir.mkdir()
    env = relay_module._isolated_env(cli, workdir)
    flat = " ".join(f"{k}={v}" for k, v in env.items())
    assert "synthetic-secret-aaa" not in flat
    assert "synthetic-secret-bbb" not in flat
    assert "synthetic-secret-ccc" not in flat
    assert "N8N_MCP_TOKEN" not in env
    assert "VAULT_LIBRARY_TOKEN" not in env
    if cli == "codex":
        assert Path(env["CODEX_HOME"]).parent == workdir
    if cli == "opencode":
        assert Path(env["XDG_CONFIG_HOME"]).parent == workdir
        config = json.loads(
            (Path(env["XDG_CONFIG_HOME"]) / "opencode.json").read_text(encoding="utf-8")
        )
        assert config["permission"] == {"edit": "deny", "bash": "deny", "webfetch": "deny"}


@pytest.mark.parametrize(
    ("cli", "tokens"),
    [
        ("claude", ["claude", "--print", "--model", "--permission-mode", "plan", "--tools"]),
        ("codex", ["codex", "exec", "-m", "read-only", "-o"]),
        ("opencode", ["opencode", "run", "-m", "--file"]),
    ],
)
def test_build_argv_contains_expected_command(
    tmp_path: Path, cli: str, tokens: list[str]
) -> None:
    prompt_file = tmp_path / f"prompt-{cli}.txt"
    prompt_file.write_text("domanda sintetica", encoding="utf-8")
    output_file = tmp_path / f"answer-{cli}.txt"
    argv, stdin_text = relay_module._build_argv(cli, "fake-model-x", prompt_file, output_file)
    assert argv[0] == cli
    for token in tokens:
        assert token in argv
    assert "fake-model-x" in argv
    if cli == "opencode":
        assert stdin_text is None
        assert str(prompt_file) in argv
    else:
        assert stdin_text is not None
        assert "domanda sintetica" in stdin_text


def test_extract_result_returns_outcome_from_fake_output(tmp_path: Path) -> None:
    output_file = tmp_path / "answer.txt"
    # claude: JSON con result.
    assert (
        relay_module._extract_result(
            "claude", json.dumps({"result": "risposta finta claude"}), output_file
        )
        == "risposta finta claude"
    )
    # claude: output non JSON passa invariato.
    assert (
        relay_module._extract_result("claude", "testo grezzo", output_file) == "testo grezzo"
    )
    # codex: vince il file di output finto.
    output_file.write_text("risposta finta codex\n", encoding="utf-8")
    assert (
        relay_module._extract_result("codex", "banner", output_file)
        == "risposta finta codex"
    )
    # codex: senza file vince lo stdout.
    output_file.unlink()
    assert (
        relay_module._extract_result("codex", "solo stdout", output_file) == "solo stdout"
    )
    # opencode: ANSI e righe di progresso ">" spariscono.
    noisy = "\x1b[0m> build \u00b7 finto\nrisposta finta opencode\n"
    assert (
        relay_module._extract_result("opencode", noisy, output_file)
        == "risposta finta opencode"
    )


def test_helpers_called_in_sequence() -> None:
    src = Path(relay_module.__file__).read_text(encoding="utf-8")
    assert "def _isolated_env" in src
    assert "def _build_argv" in src
    assert "def _extract_result" in src
    body = src[src.index("def run_relay") :]
    assert body.index("_build_argv(") < body.index("_isolated_env(")
    assert body.index("_isolated_env(") < body.index("_extract_result(")


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(relay_module.run_relay).parameters) == [
        "cfg",
        "cli",
        "model",
        "prompt",
        "attach",
        "timeout",
        "allow_outside_attach",
    ]
    assert list(inspect.signature(relay_module.available_clis).parameters) == []


def test_run_relay_wires_helpers_with_fake_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CLI finto in tmp_path: mai il PATH reale, mai esecuzione reale.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("finto", encoding="utf-8")
    monkeypatch.setenv("N8N_MCP_TOKEN", "synthetic-secret-aaa")
    monkeypatch.setenv("VAULT_LIBRARY_TOKEN", "synthetic-secret-bbb")
    monkeypatch.setattr(
        relay_module.shutil,
        "which",
        lambda name: str(bin_dir / name) if name == "codex" else None,
    )
    captured: dict = {}

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["env"] = dict(kwargs.get("env") or {})
        captured["timeout"] = kwargs.get("timeout")
        captured["cwd"] = kwargs.get("cwd")
        out = None
        prev = ""
        for token in argv:
            if prev == "-o":
                out = token
            prev = token
        assert out, "il fake codex deve ricevere -o <file>"
        Path(out).write_text("risposta finta codex\n", encoding="utf-8")
        return SimpleNamespace(stdout="banner\n", stderr="", returncode=0)

    monkeypatch.setattr(relay_module.subprocess, "run", _fake_run)
    cfg = _cfg(tmp_path)
    result = relay_module.run_relay(cfg, "codex", "fake-model", "domanda sintetica", timeout=5)
    assert result.answer == "risposta finta codex"
    assert result.returncode == 0
    assert captured["argv"][0] == "codex"
    assert "read-only" in captured["argv"]
    assert captured["timeout"] == 5
    flat = " ".join(f"{k}={v}" for k, v in captured["env"].items())
    assert "synthetic-secret-aaa" not in flat
    assert "synthetic-secret-bbb" not in flat
    assert not Path(captured["cwd"]).exists()

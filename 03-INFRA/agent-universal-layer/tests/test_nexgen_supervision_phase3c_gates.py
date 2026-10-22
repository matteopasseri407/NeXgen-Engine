"""Raccolta: il resoconto lasciato nella copia, la scansione delle fughe, il costo per harness.

Repository git veri per lo spostamento del resoconto; nessuna scansione vera di
fughe: gli script finti scrivono l'argomento che hanno ricevuto e scelgono l'esito,
così la prova riguarda l'intervallo e il verdetto, non il contenuto del progetto.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from nexgen_local import supervision_costs as costs
from nexgen_local import supervision_gates as gates
from nexgen_local import supervision_state as state
from nexgen_local import supervision_worktree as worktree

from supervision_repo import build, git_in

VERIFY_OK = f'"{sys.executable}" -c "print(1)"'
LEAK_SCRIPT = "03-INFRA/agent-universal-layer/leak-scan/leak_scan.py"
LEAK_PATTERNS = "03-INFRA/agent-universal-layer/leak-scan/leak_patterns.yaml"


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def repo(tmp_path):
    return build(tmp_path / "sandbox")


@pytest.fixture
def task(lane):
    return state.start("prova raccolta")["id"]


def _unit(task: str, repo, tmp_path: Path) -> Path:
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief dell'unità prova\n", encoding="utf-8")
    record = worktree.create(
        task, "prova", repo=repo.path, base="main", brief=str(brief),
        allow=["docs/*.md", "30-resoconto-prova.md"], verify=VERIFY_OK,
    )
    return Path(record["worktree"])


def _commit(folder: Path, files: dict[str, str], message: str = "docs: una riga") -> str:
    for name, text in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", message)
    return git_in(folder, "rev-parse", "HEAD").stdout.strip()


def _declare_leak_scan(folder: Path, body: str) -> None:
    """A project that declares CI's scan, and a fake script at the exact path the gate runs."""
    workflow = folder / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "jobs:\n  fughe:\n    steps:\n      - run: python "
        f"{LEAK_SCRIPT} --patterns {LEAK_PATTERNS}\n",
        encoding="utf-8",
    )
    script = folder / LEAK_SCRIPT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(body, encoding="utf-8")
    (folder / LEAK_PATTERNS).write_text("patterns: []\n", encoding="utf-8")


def test_collect_brings_a_report_left_inside_the_copy_to_the_task_folder(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, {"docs/guida.md": "# guida\n\nriga nuova.\n"})
    (folder / "30-resoconto-prova.md").write_text("# Resoconto\n\nfatto.\n", encoding="utf-8")

    result = gates.collect(task, "prova")

    assert "spostato in" in result["report_note"]
    target = state.open_task(task) / "30-resoconto-prova.md"
    assert target.read_text(encoding="utf-8") == "# Resoconto\n\nfatto.\n"
    assert not (folder / "30-resoconto-prova.md").exists()
    assert result["verdict"] == "PASS", "il resoconto spostato non sporca più l'albero"
    assert [row["event"] for row in state.read_events(task)].count("report_relocated") == 1


def test_a_tracked_report_stays_in_the_diff_instead_of_being_moved(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, {"30-resoconto-prova.md": "# Resoconto versionato\n"})

    note = gates.relocate_report(task, "prova", folder)

    assert "tracciato nella copia" in note
    assert (folder / "30-resoconto-prova.md").is_file()
    assert not (state.open_task(task) / "30-resoconto-prova.md").exists()


def test_a_project_without_a_declared_scan_adds_no_gate(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    base = git_in(repo.path, "rev-parse", "main").stdout.strip()
    head = _commit(folder, {"docs/guida.md": "# guida\n\nriga nuova.\n"})

    assert gates._leak_gate(folder, base, head) == []


def test_the_leak_scan_runs_on_the_units_own_interval_and_a_leak_fails_collection(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    base = git_in(repo.path, "rev-parse", "main").stdout.strip()
    head = _commit(folder, {"docs/guida.md": "# guida\n\nriga nuova.\n"})
    _declare_leak_scan(folder, (
        "import json, sys\n"
        "from pathlib import Path\n"
        "Path('argv.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        "print('fuga: un nome utente nei percorsi finti')\n"
        "sys.exit(1)\n"
    ))

    found = gates._leak_gate(folder, base, head)

    assert [result.name for result in found] == ["scansione fughe"]
    assert found[0].ok is False
    assert "un nome utente" in found[0].reason
    argv = json.loads((folder / "argv.json").read_text(encoding="utf-8"))
    assert argv[:2] == ["--patterns", str(folder / LEAK_PATTERNS)]
    assert argv[argv.index("--commit-range") + 1] == f"{base}..{head}"


def test_a_clean_leak_scan_passes_collection(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    base = git_in(repo.path, "rev-parse", "main").stdout.strip()
    head = _commit(folder, {"docs/guida.md": "# guida\n\nriga nuova.\n"})
    _declare_leak_scan(folder, "import sys\nsys.exit(0)\n")

    found = gates._leak_gate(folder, base, head)

    assert [(result.name, result.ok) for result in found] == [("scansione fughe", True)]


def test_a_declared_scan_whose_script_is_missing_is_a_failure_not_a_silence(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    base = git_in(repo.path, "rev-parse", "main").stdout.strip()
    head = _commit(folder, {"docs/guida.md": "# guida\n\nriga nuova.\n"})
    workflow = folder / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(f"steps:\n  - run: python {LEAK_SCRIPT}\n", encoding="utf-8")

    found = gates._leak_gate(folder, base, head)

    assert found[0].ok is False
    assert "mancano" in found[0].reason


def test_facts_for_sends_another_harness_to_its_adapter_and_keeps_the_tested_seam(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        costs, "measure_for",
        lambda name, session: calls.append((name, session)) or "fatti del harness",
    )

    assert gates.facts_for("") is gates.session_facts
    assert gates.facts_for("opencode") is gates.session_facts
    assert gates.facts_for("local") is gates.session_facts
    assert gates.facts_for("claude")("ses_x") == "fatti del harness"
    assert calls == [("claude", "ses_x")]

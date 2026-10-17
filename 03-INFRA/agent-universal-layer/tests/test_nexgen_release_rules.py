"""Le regole di rilascio, provate qui invece che facendo partire la CI.

Il caso che conta è il primo: il glob che viveva nello YAML accettava
`1.2.3abc` come versione, e nessuno poteva accorgersene senza taggare davvero.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.release import (  # noqa: E402 - after the path shim, as CI runs it
    ZERO_SHA,
    bump_version_files,
    compute_next_version,
    is_semver,
    newer_version,
    scan_range,
    version_matches_tag,
)
from nexgen_core.release_checks import (  # noqa: E402 - after the path shim, as CI runs it
    REQUIRED_LINE,
    check_state,
    ci_passed,
    commit_checks,
    declared_checks,
    required_annotation,
    required_checks,
    verdict,
)


@pytest.mark.parametrize("value", ["0.0.1", "1.2.3", "10.20.30", "1.2.3-rc.1", "1.2.3+build.5"])
def test_real_versions_are_accepted(value: str):
    assert is_semver(value)


@pytest.mark.parametrize("value", ["1.2.3abc", "1.2", "v1.2.3", "1.2.3.4", "", "uno.due.tre", "01.2.3"])
def test_things_that_only_look_like_versions_are_refused(value: str):
    assert not is_semver(value)


def test_tag_must_name_the_version_it_contains():
    ok, _ = version_matches_tag("1.2.3", "v1.2.3")
    assert ok
    ok, message = version_matches_tag("1.2.3", "v1.2.4")
    assert not ok
    assert "1.2.3" in message and "v1.2.4" in message


def test_a_pull_request_scans_base_to_head():
    assert scan_range("pull_request", "", "ignored", "aaa", "bbb") == "aaa..bbb"


def test_a_pull_request_without_endpoints_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        scan_range("pull_request", "", "sha")


def test_a_first_push_without_a_published_base_scans_full_ancestry():
    # Senza un "prima", l'intervallo non esiste: si guarda il commit e basta.
    assert scan_range("push", ZERO_SHA, "ccc") == "ccc"
    assert scan_range("push", "", "ccc") == "ccc"


def test_a_later_push_scans_the_span():
    assert scan_range("push", "aaa", "bbb") == "aaa..bbb"


def test_versions_compare_as_numbers_not_as_text():
    # Come testo, "0.9.0" viene dopo "0.10.0": è così che un aggiornamento
    # disponibile passa inosservato.
    assert newer_version("0.10.0", "0.9.0")
    assert not newer_version("0.9.0", "0.10.0")
    assert newer_version("v1.0.0", "0.99.2")


def test_a_final_version_outranks_its_own_pre_releases():
    """SemVer 2.0.0: dopo la -rc1 la versione finale deve poter uscire.

    Scartando il suffisso le due valgono uguale, e `release ship` rifiuta la
    2.16.0 perché "esiste già": è la -rc a bloccarla.
    """
    assert newer_version("2.16.0", "2.16.0-rc1")
    assert not newer_version("2.16.0-rc1", "2.16.0")
    assert newer_version("2.16.0-rc2", "2.16.0-rc1")
    assert newer_version("2.16.0-beta.11", "2.16.0-beta.2")
    assert newer_version("2.16.0-beta.2", "2.16.0-beta.1")
    assert newer_version("2.16.0-beta.2", "2.16.0-beta")
    assert newer_version("2.16.0-beta", "2.16.0-alpha")
    assert not newer_version("2.16.0-alpha", "2.16.0-beta")
    # SemVer: un identificatore numerico vale meno di uno alfanumerico.
    assert newer_version("2.16.0-alpha", "2.16.0-1")


def test_the_repository_version_is_a_real_version():
    version_file = Path(__file__).resolve().parents[3] / "VERSION"
    assert is_semver(version_file.read_text(encoding="utf-8"))


def test_the_version_is_declared_in_one_place_only():
    """Il pacchetto e il file VERSION non possono dire numeri diversi.

    Prima ne dicevano tre: 0.99.1 nel file, 2.0.0 nel pacchetto, 2.0.0 nel
    packaging. Il file VERSION è la fonte — è quello che il processo di
    rilascio confronta con il tag e che l'aggiornatore legge sulle macchine.
    """
    import nexgen_core

    declared = (Path(__file__).resolve().parents[3] / "VERSION").read_text(encoding="utf-8").strip()
    assert nexgen_core.__version__ == declared, (
        f"nexgen_core dichiara {nexgen_core.__version__}, il file VERSION dice {declared}"
    )


def test_no_build_residue_is_tracked():
    """Ciò che una build rigenera non può stare nel repository.

    `nexgen_engine.egg-info/` era tracciato: lo scrive `pip install -e .`, e
    da quel momento chiunque costruisca il pacchetto sporca l'albero del
    motore — che è la condizione con cui l'aggiornatore si rifiuta di
    lavorare. Stesso guasto della cartella di stato, altra provenienza.
    """
    import subprocess

    repo = Path(__file__).resolve().parents[3]
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    residue = [
        path
        for path in tracked
        if ".egg-info/" in path
        or "__pycache__/" in path
        or path.endswith((".pyc", ".pyo"))
        or path.startswith(("build/", "dist/"))
    ]
    assert not residue, (
        "file rigenerati da una build, tracciati: "
        f"{', '.join(sorted({p.split('/')[-2] for p in residue}))}"
    )


def test_the_platform_table_in_the_readme_matches_the_code():
    """Una promessa di supporto non può derivare dal codice che la mantiene.

    Il README diceva a parole quali sistemi sono supportati, in due lingue,
    e le parole erano già fuori passo: l'installer gestiva tre piattaforme,
    il README ne dichiarava due, e macOS stava nel codice senza essere
    dichiarato da nessuna parte. Ora la tabella si genera, e cambiare la
    promessa richiede di cambiare il dato.
    """
    import sys

    scripts = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from nexgen_core.platforms import END, START, render_markdown

    readme = (Path(__file__).resolve().parents[3] / "README.md").read_text(encoding="utf-8")
    assert START in readme and END in readme, "i marcatori della tabella non ci sono più"

    start = readme.index(START)
    end = readme.index(END) + len(END)
    assert readme[start:end] == render_markdown(), (
        "la tabella nel README non combacia con nexgen_core/platforms.py: "
        "rigenerala con 'python3 03-INFRA/scripts/nexgen_core/platforms.py'"
    )


def test_every_platform_the_installer_starts_on_is_declared():
    """Una piattaforma presente nel codice e assente dalla tabella è il bug
    che quel file esiste per impedire."""
    import sys

    scripts = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from nexgen_core.platforms import OPERATING_SYSTEMS

    declared = {s.name for s in OPERATING_SYSTEMS}
    bootstrap = (scripts / "nexgen_core" / "bootstrap.py").read_text(encoding="utf-8")
    handled = {
        "macOS" if token == "Darwin" else token
        for token in ("Linux", "Darwin", "Windows")
        if f'"{token}"' in bootstrap
    }
    assert handled <= declared, (
        f"l'installer parte su {sorted(handled - declared)} ma il README non lo dichiara"
    )


def test_compute_next_version():
    assert compute_next_version("2.0.2", "patch") == "2.0.3"
    assert compute_next_version("2.0.2", "hotfix") == "2.0.3"
    assert compute_next_version("2.0.2", "minor") == "2.1.0"
    assert compute_next_version("2.0.2", "major") == "3.0.0"
    assert compute_next_version("2.0.2", "2.0.5") == "2.0.5"
    assert compute_next_version("v2.0.2", "patch") == "2.0.3"


def test_bump_version_files(tmp_path: Path):
    version_file = tmp_path / "VERSION"
    version_file.write_text("2.0.2\n", encoding="utf-8")

    core_dir = tmp_path / "03-INFRA" / "scripts" / "nexgen_core"
    core_dir.mkdir(parents=True)
    init_file = core_dir / "__init__.py"
    init_file.write_text('__version__ = "2.0.2"\n', encoding="utf-8")

    cl_file = tmp_path / "CHANGELOG.md"
    cl_file.write_text("# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- Some fix.\n", encoding="utf-8")

    modified = bump_version_files(tmp_path, "2.0.3")
    assert version_file in modified
    assert version_file.read_text(encoding="utf-8").strip() == "2.0.3"
    assert '__version__ = "2.0.3"' in init_file.read_text(encoding="utf-8")
    assert "## [2.0.3]" in cl_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("unreleased_body", "notes", "expected_body"),
    [
        ("", "Fixed the thing", "### Changed\n\n- Fixed the thing"),
        ("### Added\n\n- A curated entry.", "", "### Added\n\n- A curated entry."),
    ],
)
def test_the_unreleased_section_becomes_the_new_version_and_is_left_empty(tmp_path, unreleased_body, notes, expected_body):
    (tmp_path / "VERSION").write_text("2.0.2\n", encoding="utf-8")
    cl_file = tmp_path / "CHANGELOG.md"
    cl_file.write_text(
        f"# Changelog\n\n## [Unreleased]\n\n{unreleased_body}\n\n## [2.0.2] - 2026-01-01\n\n- Older.\n",
        encoding="utf-8",
    )

    bump_version_files(tmp_path, "2.0.3", notes)

    text = cl_file.read_text(encoding="utf-8")
    assert text.index("## [Unreleased]") < text.index("## [2.0.3]") < text.index("## [2.0.2]")
    between_unreleased_and_release = text.split("## [Unreleased]", 1)[1].split("## [2.0.3]", 1)[0]
    assert between_unreleased_and_release.strip() == ""
    new_release_body = text.split("## [2.0.3]", 1)[1].split("## [2.0.2]", 1)[0]
    assert expected_body in new_release_body


def test_first_push_checks_all_unpublished_commits_and_excludes_published_history(tmp_path):
    import subprocess
    import secrets
    import runpy
    scanner = SCRIPTS_DIR.parent / "agent-universal-layer/leak-scan/leak_scan.py"
    units_from_commit_range = runpy.run_path(str(scanner))["units_from_commit_range"]
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.com")
    git("config", "commit.gpgsign", "false")
    target = repo / "fixture.txt"
    target.write_text("published legacy fixture")
    git("add", ".")
    git("commit", "-qm", "published baseline")
    base = git("rev-parse", "HEAD")
    marker = secrets.token_hex(24)
    target.write_text(marker)
    git("add", ".")
    git("commit", "-qm", "first new commit")
    target.write_text("clean final state")
    git("add", ".")
    git("commit", "-qm", "second new commit")
    head = git("rev-parse", "HEAD")
    selected = scan_range("push", ZERO_SHA, head, first_push_base=base)
    units = units_from_commit_range(str(repo), selected)
    text = "\n".join(unit.text for unit in units)
    assert marker in text  # a value removed at the tip is still examined
    assert "clean final state" in text
    assert "published legacy fixture" not in text


def test_a_bump_with_nothing_to_say_is_refused_before_any_write(tmp_path):
    (tmp_path / "VERSION").write_text("2.0.2\n", encoding="utf-8")
    cl_file = tmp_path / "CHANGELOG.md"
    original = "# Changelog\n\n## [Unreleased]\n\n## [2.0.2] - 2026-01-01\n\n- Older.\n"
    cl_file.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="Unreleased"):
        bump_version_files(tmp_path, "2.0.3")

    assert (tmp_path / "VERSION").read_text(encoding="utf-8") == "2.0.2\n"
    assert cl_file.read_text(encoding="utf-8") == original


def test_a_force_pushed_before_falls_back_to_the_published_base():
    sha = "c" * 40
    assert scan_range("push", "a" * 40, sha, first_push_base="origin/main", before_present=False) == f"origin/main..{sha}"
    assert scan_range("push", "a" * 40, sha, before_present=False) == sha
    assert scan_range("push", "a" * 40, sha, first_push_base="origin/main") == f"{'a' * 40}..{sha}"


# --- the checks main requires, and whether a commit carries them ---------------


class FakeGitHub:
    """A scripted GitHub: what the protection and the rules declare, and what one commit carries."""

    def __init__(self, required=("engine-tests",), protection_ok=True, rules_ok=True, runs=None, ci_success=1):
        self.required = list(required)
        self.protection_ok = protection_ok
        self.rules_ok = rules_ok
        self.runs = runs if runs is not None else {}
        self.ci_success = ci_success
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, cwd):
        argv = tuple(str(a) for a in argv)
        self.calls.append(argv)
        code, out = self._answer(argv)
        return subprocess.CompletedProcess(argv, code, out, "")

    def _answer(self, argv) -> tuple[int, str]:
        url = next((arg for arg in argv[2:] if arg.startswith("repos/")), "")
        if url.endswith("required_status_checks"):
            if not self.protection_ok:
                return 1, ""
            return 0, json.dumps({"contexts": self.required, "checks": []})
        if url.endswith("/rules/branches/main"):
            if not self.rules_ok:
                return 1, ""
            rules = [{"type": "required_status_checks",
                      "parameters": {"required_status_checks": [{"context": name} for name in self.required]}}]
            return 0, json.dumps(rules)
        if "/check-runs" in url:
            sha = url.split("/")[-2]
            runs = self.runs.get(sha)
            if runs is None:
                runs = [{"name": name, "status": "completed", "conclusion": "success"} for name in self.required]
            return 0, json.dumps({"check_runs": runs})
        if "actions/workflows" in url:
            return 0, f"{self.ci_success}\n"
        return 0, ""


def _check(state: str, name: str = "engine-tests") -> dict:
    return {"name": name, "status": "completed", "conclusion": state}


def test_a_missing_or_running_check_is_not_a_failure_yet():
    assert check_state(None) is None
    assert check_state({"name": "x", "status": "in_progress"}) is None
    assert check_state(_check("success")) is True
    assert check_state(_check("failure")) is False
    assert verdict({"engine-tests": _check("success")}, {"engine-tests"}) == ("green", [])


def test_the_verdict_names_what_failed_and_what_has_not_reported():
    assert verdict({"engine-tests": _check("failure")}, {"engine-tests"}) == ("failed", ["engine-tests"])
    assert verdict({}, {"engine-tests"}) == ("missing", ["engine-tests"])
    assert verdict({"other": _check("success")}, {"engine-tests"}) == ("missing", ["engine-tests"])


def test_the_required_checks_are_read_from_both_places_github_keeps_them(tmp_path):
    github = FakeGitHub(required=["engine-tests", "vault-mcp-smoke"])
    assert required_checks(tmp_path, github) == {"engine-tests", "vault-mcp-smoke"}
    assert any("protection" in " ".join(call) for call in github.calls)
    assert any("rules/branches" in " ".join(call) for call in github.calls)


def test_a_protection_a_token_cannot_read_still_gives_the_rules_own_answer(tmp_path):
    """A workflow token has no admin rights: the rules endpoint is the one it can read."""
    github = FakeGitHub(required=["engine-tests"], protection_ok=False)
    assert required_checks(tmp_path, github) == {"engine-tests"}
    assert required_checks(tmp_path, FakeGitHub(protection_ok=False, rules_ok=False)) is None


def test_one_commit_carries_its_checks_by_name(tmp_path):
    github = FakeGitHub(required=["engine-tests"])
    assert commit_checks(tmp_path, "a" * 40, github) == {
        "engine-tests": {"name": "engine-tests", "status": "completed", "conclusion": "success"}}


def test_the_list_travels_in_the_tag_annotation_and_comes_back(tmp_path):
    annotation = required_annotation({"test", "engine-tests"})
    assert REQUIRED_LINE in annotation[1]
    assert declared_checks("Release v1.2.3\n\n" + annotation[1]) == {"test", "engine-tests"}
    assert declared_checks("Release v1.2.3") is None
    assert required_annotation(None) == []


def test_a_ship_tag_is_accepted_when_its_commit_carries_the_checks(tmp_path):
    github = FakeGitHub(required=["engine-tests"])
    message = "Release v1.2.3\n\n" + required_annotation({"engine-tests"})[1]
    runner = _tag_runner(github, message)
    ok, reason = ci_passed(tmp_path, "v1.2.3", run=runner, wait_seconds=0)
    assert ok and "engine-tests" in reason


def test_a_ship_tag_is_refused_when_a_required_check_failed(tmp_path):
    github = FakeGitHub(required=["engine-tests"], runs={"a" * 40: [_check("failure")]})
    message = "Release v1.2.3\n\n" + required_annotation({"engine-tests"})[1]
    ok, reason = ci_passed(tmp_path, "v1.2.3", run=_tag_runner(github, message), wait_seconds=0)
    assert not ok and "engine-tests" in reason


def test_a_required_check_that_has_not_reported_is_waited_for(tmp_path):
    github = FakeGitHub(required=["engine-tests"], runs={"a" * 40: []})
    message = "Release v1.2.3\n\n" + required_annotation({"engine-tests"})[1]
    slept: list[float] = []
    ok, reason = ci_passed(tmp_path, "v1.2.3", run=_tag_runner(github, message), wait_seconds=60,
                           sleep=slept.append)
    assert not ok and "not reported" in reason
    assert slept, "a missing required check is waited for, not refused at once"


def test_a_hand_cut_tag_is_judged_by_its_ci_run(tmp_path):
    """The documented hand-cut flow (`git tag -s -m "Release vX"`) has no list, and
    keeps the rule this gate started with: one successful ci.yml run."""
    ok, reason = ci_passed(tmp_path, "v1.2.3", run=_tag_runner(FakeGitHub(ci_success=1), "Release v1.2.3"))
    assert ok and "ci.yml" in reason
    refused, why = ci_passed(tmp_path, "v1.2.3", run=_tag_runner(FakeGitHub(ci_success=0), "Release v1.2.3"))
    assert not refused and "no successful run" in why


def test_a_tag_that_is_not_in_this_checkout_is_refused_not_guessed(tmp_path):
    """`git rev-parse` echoes a missing tag's name on stdout, so the answer is checked, not assumed."""
    github = FakeGitHub()
    runner = _tag_runner(github, "Release v1.2.3")
    original = runner

    def run(argv, cwd):
        argv = tuple(str(a) for a in argv)
        if argv[:2] == ("git", "-C") and "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 128, "v1.2.3^{commit}", "")
        return original(argv, cwd)

    ok, reason = ci_passed(tmp_path, "v1.2.3", run=run)
    assert not ok and "not a tag" in reason


def _tag_runner(github: FakeGitHub, message: str):
    """The api answers above, plus the two git reads `ci_passed` makes."""

    def run(argv, cwd):
        argv = tuple(str(a) for a in argv)
        if argv[:2] == ("git", "-C") and "%(contents:body)" in " ".join(argv):
            return subprocess.CompletedProcess(argv, 0, message, "")
        if argv[:2] == ("git", "-C") and "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, ("a" * 40) + "\n", "")
        return github(argv, cwd)

    return run


def test_the_release_workflow_refuses_with_the_rules_own_verdict():
    """The step that keeps a stray tag from becoming a release calls the Python rule:
    one place decides what "has passed CI" means, for ship and for the workflow."""
    workflow = (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    step = workflow.split("Refuse a commit that has not passed CI", 1)[1].split("- name:", 1)[0]
    assert "release.py ci-passed" in step
    assert "--tag" in step

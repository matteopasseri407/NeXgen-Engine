"""Le regole di rilascio, provate qui invece che facendo partire la CI.

Il caso che conta è il primo: il glob che viveva nello YAML accettava
`1.2.3abc` come versione, e nessuno poteva accorgersene senza taggare davvero.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.release import ZERO_SHA, is_semver, newer_version, scan_range, version_matches_tag


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


def test_a_first_push_scans_the_single_commit():
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

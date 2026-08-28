"""I pacchetti di distribuzione: la formula Homebrew generata, non scritta
a mano. Tre regole: url del solo asset di release verificato con sha256,
dipendenze dichiarate come risorse pinnate (Homebrew vieta la rete in
install), e il workflow che pubblica non promette canali che non esistono
(leggilo come dato: i step PyPI e tap sono gateati sui rispettivi segreti).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = REPO_ROOT / "03-INFRA" / "scripts" / "release_packages.py"

FAKE_PYPI = ("https://files.pythonhosted.org/packages/source/P/PyYAML/PyYAML-6.0.2.tar.gz",
             "a" * 64)


def _generate(tmp_path: Path, **overrides) -> Path:
    import sys

    if str(GENERATOR.parent) not in sys.path:
        sys.path.insert(0, str(GENERATOR.parent))
    import release_packages

    release_packages._pyyaml_pinned = lambda: FAKE_PYPI  # niente rete nei test

    argv = [
        "--version", overrides.get("version", "2.2.0"),
        "--asset-url", overrides.get("asset_url", "https://github.com/o/r/releases/download/v2.2.0/nexgen_engine-2.2.0.tar.gz"),
        "--asset-sha256", overrides.get("asset_sha256", "b" * 64),
        "--out-dir", str(tmp_path / "Formula"),
    ]
    if "argv" in overrides:
        argv = overrides["argv"]
    release_packages.main(argv)
    return tmp_path / "Formula" / "nexgen.rb"


def test_the_formula_pins_the_release_asset_and_the_dependency(tmp_path):
    formula = _generate(tmp_path).read_text(encoding="utf-8")

    assert 'version "2.2.0"' in formula
    assert 'url "https://github.com/o/r/releases/download/v2.2.0/nexgen_engine-2.2.0.tar.gz"' in formula
    assert 'sha256 "' + "b" * 64 + '"' in formula
    # la risorsa pinnata porta l'hash letto da PyPI, mai a mano
    assert FAKE_PYPI[0] in formula
    assert 'sha256 "' + "a" * 64 + '"' in formula


def test_no_asset_sha256_means_no_formula(tmp_path):
    with pytest.raises(SystemExit):
        _generate(tmp_path, argv=["--version", "2.2.0", "--asset-url", "https://x/y.tar.gz",
                                  "--asset-sha256", "", "--out-dir", str(tmp_path / "F")])


def test_the_release_workflow_builds_and_uploads_the_artifacts():
    """Ciò che docs/release-packages.md promette, il workflow lo fa."""
    wf = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["publish"]["steps"]
    names = [s.get("name", "") for s in steps]
    runs = [s.get("run", "") for s in steps if isinstance(s.get("run"), str)]
    every_run = "\n".join(runs)

    assert "Build distribution artifacts" in names
    assert "Checksums" in names
    assert "python -m build" in every_run
    assert "sha256sum" in every_run
    assert "gh release upload" in every_run


def test_the_external_channels_are_gated_on_their_secrets():
    """Finché il maintainer non registra il token, i canali sono uno skip
    visibile: mai un fallimento, mai una promessa di pubblicazione vuota."""
    wf = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["publish"]["steps"]

    by_name = {s.get("name", ""): s for s in steps}
    tap = by_name["Update the Homebrew tap"]
    pypi = by_name["Publish to PyPI"]
    assert "env.HOMEBREW_TAP_TOKEN != ''" in tap["if"]
    assert "env.PYPI_API_TOKEN != ''" in pypi["if"]
    assert "release_packages.py" in tap["run"]

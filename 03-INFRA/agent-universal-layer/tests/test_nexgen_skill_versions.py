"""An unusable version record must never certify a skill update."""
from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest

from nexgen_core import skill_sources
from nexgen_core.checks.skill_checks import check_skills_pin_freshness
from nexgen_core.report import Severity
from nexgen_core.skill_sources import SkillEntry, SkillFetcher
from nexgen_core.skills import SkillMaterializer


@pytest.mark.parametrize("content", [b"{broken", b"\xff", b"[]", b'{"previous": 7}'])
def test_invalid_version_record_is_preserved_on_write(tmp_path, content):
    fetcher = SkillFetcher(home=tmp_path)
    record = fetcher._installed_versions_file()
    record.parent.mkdir(parents=True)
    record.write_bytes(content)
    with pytest.raises(OSError):
        fetcher._record_installed_version("demo", "2")
    assert record.read_bytes() == content


@pytest.mark.parametrize("claimed", [False, True])
def test_installer_cannot_report_success_when_pin_write_fails(tmp_path, monkeypatch, claimed):
    fetcher = SkillFetcher(home=tmp_path / "home")
    library = tmp_path / "library" / "demo"
    library.mkdir(parents=True)
    (library / "SKILL.md").write_text("old", encoding="utf-8")
    discovery = tmp_path / "discovery"
    target = discovery / "demo" if claimed else library
    target.parent.mkdir(parents=True, exist_ok=True)
    fetcher._record_installed_version("demo", "1")
    previous = fetcher._installed_versions_file().read_bytes()
    script = (
        "from pathlib import Path; "
        f"p=Path({str(target)!r}); p.mkdir(parents=True, exist_ok=True); "
        "(p/'SKILL.md').write_text('new', encoding='utf-8')"
    )

    def failed_write(*args, **kwargs):
        raise OSError("synthetic private payload")

    monkeypatch.setattr(skill_sources, "atomic_write_text", failed_write)
    entry = SkillEntry("demo", origin="installer", version="2", install=[sys.executable, "-c", script])
    ok, message = fetcher.install_third_party(entry, library, (discovery,))
    assert not ok
    assert message.startswith("[ERROR]")
    assert "synthetic private payload" not in message
    assert fetcher._installed_versions_file().read_bytes() == previous


def test_github_materialization_reports_pin_write_failure(tmp_path, monkeypatch):
    mat = SkillMaterializer(vault_data=tmp_path / "vault", home=tmp_path / "home")
    pin = "a" * 40
    entry = SkillEntry("demo", origin="github", repo="synthetic/repo", commit=pin)
    cache = mat.home / ".agents" / "cache" / "github-skills" / "demo"
    cache.mkdir(parents=True)
    (cache / "SKILL.md").write_text("verified bytes", encoding="utf-8")
    monkeypatch.setattr(mat, "load_manifest", lambda: {"demo": entry})
    monkeypatch.setattr(mat.fetcher, "ensure_github_checkout", lambda *args: (True, None))

    def failed_write(*args, **kwargs):
        raise PermissionError("synthetic private payload")

    monkeypatch.setattr(skill_sources, "atomic_write_text", failed_write)
    _, actions = mat.materialize(apply=True)
    assert any(action.startswith("[ERROR]") and "demo" in action for action in actions)
    assert all("synthetic private payload" not in action for action in actions)
    assert not mat.fetcher._installed_versions_file().exists()


def test_doctor_reports_unreadable_version_record_as_broken(tmp_path):
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    record = SkillFetcher(home=home)._installed_versions_file()
    record.parent.mkdir(parents=True)
    record.write_text('{"demo": "synthetic private payload", broken}', encoding="utf-8")
    outcome = check_skills_pin_freshness(vault, home)
    assert outcome.severity == Severity.BROKEN
    assert "synthetic private payload" not in outcome.message
    assert record.read_text(encoding="utf-8").endswith("broken}")


def test_version_write_keeps_other_skill_pins(tmp_path):
    fetcher = SkillFetcher(home=tmp_path)
    fetcher._record_installed_version("first", "1")
    fetcher._record_installed_version("second", "2")
    assert json.loads(fetcher._installed_versions_file().read_text(encoding="utf-8")) == {"first": "1", "second": "2"}


def test_concurrent_pin_writes_keep_both_versions(tmp_path, monkeypatch):
    fetcher = SkillFetcher(home=tmp_path)
    fetcher._record_installed_version("existing", "0")
    read_ready = threading.Event()
    resume = threading.Event()
    writer = threading.local()
    real_read = fetcher._installed_versions

    def slow_read():
        current = real_read()
        if getattr(writer, "first", False):
            read_ready.set()
            assert resume.wait(timeout=5)
        return current

    def first_write():
        writer.first = True
        fetcher._record_installed_version("first", "1")

    monkeypatch.setattr(fetcher, "_installed_versions", slow_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_write)
        try:
            assert read_ready.wait(timeout=3)
            second = pool.submit(fetcher._record_installed_version, "second", "2")
            try:
                second.result(timeout=0.3)
            except TimeoutError:
                pass  # A serialized writer waits until the first completes.
        finally:
            resume.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert fetcher._installed_versions() == {"existing": "0", "first": "1", "second": "2"}

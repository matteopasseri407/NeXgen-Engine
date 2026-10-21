"""La fiducia delle cartelle: una voce esatta, aggiunta una volta, con la copia di prima.

Tutto gira su una home temporanea, quindi nessun test legge o tocca le
impostazioni vere dell'esploratore. Quello che si prova è il contratto: solo
`trustedWorkspaces` viene scritta, il resto del file resta com'era, e un file che
non si legge è un errore invece di un file da sovrascrivere.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexgen_local import supervision_trust as trust


@pytest.fixture
def home(tmp_path):
    """Una home finta: le impostazioni nascono qui dentro."""
    return tmp_path / "home"


@pytest.fixture
def settings(home):
    return trust.settings_path(home)


def _written(settings: Path) -> dict:
    return json.loads(settings.read_text(encoding="utf-8"))


def test_the_settings_live_under_the_home_of_whoever_runs_it(home):
    assert trust.settings_path(home) == home / ".gemini" / "antigravity-cli" / "settings.json"


def test_a_folder_is_added_and_the_answer_says_it_was_written(tmp_path, settings):
    folder = tmp_path / "compito" / "24-ricognizione"

    result = trust.ensure(folder, settings=settings)

    assert result["added"] is True
    assert result["folder"] == str(folder.resolve())
    assert result["backup"] == ""
    assert _written(settings)[trust.KEY] == [str(folder.resolve())]


def test_the_same_folder_twice_writes_nothing_and_leaves_one_entry(tmp_path, settings):
    folder = tmp_path / "ricognizione"
    trust.ensure(folder, settings=settings)
    before = settings.read_bytes()

    second = trust.ensure(folder, settings=settings)

    assert second["added"] is False
    assert settings.read_bytes() == before
    assert _written(settings)[trust.KEY] == [str(folder.resolve())]


def test_every_other_setting_is_kept_and_the_previous_file_is_copied(tmp_path, settings):
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"theme": "scuro", "trustedWorkspaces": ["C:/altro"]}), encoding="utf-8",
    )
    before = settings.read_bytes()

    result = trust.ensure(tmp_path / "nuova", settings=settings)

    assert result["added"] is True
    payload = _written(settings)
    assert payload["theme"] == "scuro"
    assert payload["trustedWorkspaces"] == ["C:/altro", str((tmp_path / "nuova").resolve())]
    backup = Path(result["backup"])
    assert backup == settings.with_name(settings.name + trust.BACKUP_SUFFIX)
    assert backup.read_bytes() == before


def test_only_the_exact_folder_is_trusted_and_a_parent_does_not_cover_it(tmp_path, settings):
    parent = tmp_path / "lavagna"
    trust.ensure(parent, settings=settings)

    assert trust.inside(parent, settings=settings) is True
    assert trust.inside(parent / "sotto", settings=settings) is False


def test_asking_without_the_key_creates_no_file(tmp_path, settings):
    assert trust.inside(tmp_path / "mai-vista", settings=settings) is False
    assert not settings.exists()


def test_a_trailing_separator_is_the_same_folder(tmp_path, settings):
    folder = tmp_path / "con-separatore"
    trust.ensure(folder, settings=settings)

    assert trust.inside(str(folder) + "/", settings=settings) is True
    assert len(_written(settings)[trust.KEY]) == 1


def test_a_settings_file_that_does_not_parse_is_refused_and_left_alone(tmp_path, settings):
    settings.parent.mkdir(parents=True)
    settings.write_text("{non json", encoding="utf-8")
    before = settings.read_bytes()

    with pytest.raises(trust.TrustError):
        trust.ensure(tmp_path / "copia", settings=settings)

    assert settings.read_bytes() == before


def test_a_list_of_something_else_is_refused_instead_of_fixed(tmp_path, settings):
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({trust.KEY: [1, 2]}), encoding="utf-8")

    with pytest.raises(trust.TrustError):
        trust.ensure(tmp_path / "copia", settings=settings)


def test_a_file_that_is_not_an_object_is_refused(tmp_path, settings):
    settings.parent.mkdir(parents=True)
    settings.write_text("[]", encoding="utf-8")

    with pytest.raises(trust.TrustError):
        trust.ensure(tmp_path / "copia", settings=settings)

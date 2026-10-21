"""L'elenco dei modelli consentiti: schemi, nessun valore personale, un file solo.

La configurazione vive nella lane temporanea del test; il file è l'unica fonte
e un file che non si legge è un errore, non un via libera.
"""
from __future__ import annotations

import json

import pytest

from nexgen_local import supervision_policy as policy
from nexgen_local import supervision_state as state


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """La lane in una cartella temporanea: la configurazione è quella che scrive il test."""
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane"


def _write(lane, payload) -> None:
    lane.mkdir(parents=True, exist_ok=True)
    (lane / policy.CONFIG_FILE).write_text(json.dumps(payload), encoding="utf-8")


def test_a_lane_without_a_settings_file_has_no_list(lane):
    assert policy.config_path() == lane / "config.json"
    assert policy.allowed_models() == ()
    assert policy.model_allowed("chiunque/modello#variante")


def test_an_empty_list_means_no_restriction(lane):
    _write(lane, {"supervision": {"allowed_models": []}})

    assert policy.allowed_models() == ()
    assert policy.model_allowed("chiunque/modello")


def test_the_list_is_read_as_written(lane):
    _write(lane, {"supervision": {"allowed_models": ["opencode-go/*", "locale/*"]}})

    assert policy.allowed_models() == ("opencode-go/*", "locale/*")


def test_a_settings_file_that_is_not_json_is_an_error_not_a_free_pass(lane):
    """Leggerlo come «nessuna restrizione» consentirebbe proprio ciò che l'elenco vieta."""
    lane.mkdir(parents=True, exist_ok=True)
    (lane / policy.CONFIG_FILE).write_text("{non json", encoding="utf-8")

    with pytest.raises(policy.PolicyError, match="non è JSON"):
        policy.allowed_models()


def test_a_list_that_is_not_a_list_of_strings_is_an_error(lane):
    _write(lane, {"supervision": {"allowed_models": "opencode-go/*"}})

    with pytest.raises(policy.PolicyError, match="elenco di schemi"):
        policy.allowed_models()


def test_a_section_that_is_not_an_object_is_an_error(lane):
    _write(lane, {"supervision": ["opencode-go/*"]})

    with pytest.raises(policy.PolicyError, match="non è un oggetto"):
        policy.allowed_models()


def test_the_glob_covers_the_full_tag_and_the_part_before_the_variant(lane):
    allowed = ("opencode-go/*",)

    assert policy.model_allowed("opencode-go/deepseek-v4.1-flash#max", allowed)
    assert policy.model_allowed("opencode-go/qualsiasi-cosa", allowed)
    assert not policy.model_allowed("altro-provider/modello", allowed)


def test_a_pattern_can_name_the_variant(lane):
    allowed = ("opencode-go/*#max",)

    assert policy.model_allowed("opencode-go/modello#max", allowed)
    assert not policy.model_allowed("opencode-go/modello#min", allowed)


def test_a_model_nobody_can_read_is_not_on_a_list(lane):
    """Una sessione che il motore non sa leggere non è una sessione che può approvare."""
    assert not policy.model_allowed("", ("opencode-go/*",))


def test_the_refusal_names_the_model_and_the_list():
    reason = policy.refusal("altro/modello", ("opencode-go/*",))

    assert "altro/modello" in reason
    assert "opencode-go/*" in reason
    assert "fuori dall'elenco consentito" in reason

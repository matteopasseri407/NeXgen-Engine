"""Lo stack locale: le regole, non l'output.

Nessun test qui avvia Docker o tocca la rete. Ciò che va garantito è che i
segreti non vengano mai rigenerati sopra a uno esistente, che non finiscano
in un messaggio, e che i permessi del file restino stretti.
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.stack import runner, secrets
from nexgen_core.stack.services import SERVICES, by_name


def test_secrets_are_generated_only_when_missing(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("ESISTENTE=non-toccarmi\n", encoding="utf-8")

    generated = secrets.ensure(env, ["ESISTENTE", "NUOVO"])

    assert generated == ["NUOVO"], "un segreto già presente non va rigenerato"
    values = secrets.read_env_file(env)
    assert values["ESISTENTE"] == "non-toccarmi"
    assert len(values["NUOVO"]) == 64


def test_secrets_file_is_not_world_readable(tmp_path: Path):
    env = tmp_path / ".env"
    secrets.ensure(env, ["UN_SEGRETO"])
    mode = env.stat().st_mode
    assert not mode & stat.S_IRGRP
    assert not mode & stat.S_IROTH


def test_workstation_env_is_not_world_readable(tmp_path: Path):
    conf = tmp_path / "workstation.conf"
    secrets.write_workstation_env(conf, {"KEY": "VAL"})
    mode = conf.stat().st_mode
    assert not mode & stat.S_IRGRP
    assert not mode & stat.S_IROTH



def test_generated_names_never_carry_their_values(tmp_path: Path):
    env = tmp_path / ".env"
    generated = secrets.ensure(env, ["UN_SEGRETO"])
    value = secrets.read_env_file(env)["UN_SEGRETO"]
    # La funzione restituisce nomi: un valore che passa da un valore di
    # ritorno finisce prima o poi in un messaggio o in un log.
    assert generated == ["UN_SEGRETO"]
    assert value not in "".join(generated)


def test_exports_resolve_ports_and_tokens(tmp_path: Path):
    env_values = {"VAULT_LIBRARY_TOKEN": "abc123"}
    service = by_name("vault-library")
    assert service is not None

    resolved = secrets.resolve_exports(service.exports, service.port, env_values)

    assert resolved["VAULT_LIBRARY_URL"] == f"http://127.0.0.1:{service.port}/mcp"
    assert resolved["VAULT_LIBRARY_TOKEN"] == "abc123"


def test_export_without_its_secret_is_omitted_not_empty():
    service = by_name("vault-library")
    assert service is not None
    resolved = secrets.resolve_exports(service.exports, service.port, {})
    # Esportare un token vuoto farebbe montare il connettore in uno stato che
    # fallisce all'uso invece di non montarlo affatto.
    assert "VAULT_LIBRARY_TOKEN" not in resolved


def test_workstation_env_is_regenerated_whole(tmp_path: Path):
    target = tmp_path / "90-nexgen-stack.conf"
    secrets.write_workstation_env(target, {"A": "1", "B": "2"})
    secrets.write_workstation_env(target, {"A": "1"})
    body = target.read_text(encoding="utf-8")
    assert "A=1" in body
    assert "B=2" not in body, "una voce tolta dallo stack deve sparire davvero"


def test_unknown_service_names_the_available_ones():
    with pytest.raises(runner.StackError) as exc:
        runner.selected(["inesistente"])
    message = str(exc.value)
    for service in SERVICES:
        assert service.name in message


def test_no_selection_means_every_service():
    assert runner.selected(None) == list(SERVICES)


def test_every_service_declares_a_compose_file_that_exists():
    engine_root = Path(__file__).resolve().parents[2]
    for service in SERVICES:
        from nexgen_core.stack.services import compose_file

        assert compose_file(engine_root, service).is_file(), (
            f"{service.name} dichiara uno stack che non esiste nel repo"
        )

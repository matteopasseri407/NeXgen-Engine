"""Gli MCP che l'unità non ha: spenti davvero, nella forma che lo schema definisce.

La forma non si indovina. Lo schema che il motore stesso nomina in `$schema`
(https://opencode.ai/config.json, `$defs.Mcp.LocalConfigEncoded` e
`$defs.Mcp.RemoteConfigEncoded`) vuole la tabella nativa `mcp.servers.<nome>` e la
chiave `disabled`; il layout piatto `mcp.<nome>` con `enabled` è quello deprecato che
il motore migra. Entrambe le definizioni hanno `additionalProperties: false`: una
chiave che non nominano invalida la voce, e una voce invalida fa rifiutare il file
del progetto, quindi i server globali ripartono. Questi test tengono la scrittura
dentro quella forma.

Nessun OpenCode viene avviato: è il file scritto nella copia che si legge.
"""
from __future__ import annotations

import json

import pytest

from nexgen_local import supervision_permissions as permissions
from nexgen_local import supervision_state as state

#: Le chiavi che le due definizioni dello schema nominano. `required` è ciò che la
#: voce non può non avere, `allowed` tutto ciò che `additionalProperties: false`
#: lascia passare. Copiato a mano dallo schema, non derivato dal codice del motore.
ENCODED = {
    "local": {
        "required": {"type", "command"},
        "allowed": {"type", "command", "cwd", "environment", "disabled", "codemode",
                    "timeout", "protocol"},
    },
    "remote": {
        "required": {"type", "url"},
        "allowed": {"type", "url", "headers", "oauth", "disabled", "codemode",
                    "timeout", "protocol"},
    },
}

GLOBAL = {
    "vault-library": {
        "type": "remote", "url": "{env:VAULT_LIBRARY_URL}",
        "headers": {"Authorization": "Bearer {env:VAULT_LIBRARY_TOKEN}"}, "oauth": False,
    },
    "firecrawl": {
        "type": "local", "command": ["python", "mcp-trim.py", "firecrawl"],
        "environment": {"AGENT_VAULT_DATA": "/vault"},
    },
}


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane"


@pytest.fixture
def task(lane):
    return state.start("prova MCP dell'unità")["id"]


@pytest.fixture
def copy(tmp_path):
    folder = tmp_path / "unita"
    folder.mkdir()
    return folder


def _written(task, copy, *, mcp=(), global_servers=None) -> dict:
    target = permissions.write_profile(
        task, copy, mcp=list(mcp),
        global_servers=GLOBAL if global_servers is None else global_servers,
    )
    return json.loads(target.read_text(encoding="utf-8"))["mcp"]["servers"]


def _check_schema(entry: dict) -> None:
    """La voce come la vedrebbe lo schema: tipo previsto, obbligatorie presenti, nient'altro."""
    rule = ENCODED.get(str(entry.get("type") or ""))
    assert rule is not None, f"tipo che lo schema non prevede: {entry}"
    assert rule["required"] <= set(entry), f"mancano campi obbligatori: {entry}"
    extra = set(entry) - rule["allowed"]
    assert not extra, f"chiavi che la voce rifiuta: {sorted(extra)}"
    assert isinstance(entry.get("disabled"), bool)


# --- la forma che spegne ------------------------------------------------------


def test_every_unassigned_server_is_switched_off_in_the_native_shape(task, copy):
    servers = _written(task, copy)

    assert set(servers) == set(GLOBAL)
    for entry in servers.values():
        _check_schema(entry)
        assert entry["disabled"] is True


def test_the_switch_is_disabled_and_never_the_deprecated_enabled(task, copy):
    servers = _written(task, copy)

    for entry in servers.values():
        assert "enabled" not in entry
        assert entry["disabled"] is True


def test_an_assigned_server_is_left_on_without_a_switch_in_the_copy(task, copy):
    servers = _written(task, copy, mcp=["firecrawl"])

    assert "firecrawl" not in servers
    assert servers["vault-library"]["disabled"] is True


def test_an_assigned_server_the_global_file_disables_is_written_enabled(task, copy):
    flat = {"firecrawl": {"type": "local", "command": ["python", "mcp-trim.py"], "enabled": False}}

    servers = _written(task, copy, mcp=["firecrawl"], global_servers=flat)

    _check_schema(servers["firecrawl"])
    assert servers["firecrawl"]["disabled"] is False


def test_a_global_file_in_the_flat_layout_is_read_and_rewritten_natively(task, copy):
    """La macchina non ancora rigenerata dichiara `enabled`: la copia scrive `disabled`."""
    flat = {
        "firecrawl": {"type": "local", "command": ["python", "mcp-trim.py"], "enabled": False},
        "vault": {"type": "remote", "url": "https://example.invalid/mcp", "enabled": True},
    }

    servers = _written(task, copy, global_servers=flat)

    assert servers["firecrawl"] == {"type": "local", "command": ["python", "mcp-trim.py"], "disabled": True}
    assert servers["vault"] == {"type": "remote", "url": "https://example.invalid/mcp", "disabled": True}
    for entry in servers.values():
        _check_schema(entry)


def test_the_copy_carries_no_credential_or_environment_of_the_global_file(task, copy):
    servers = _written(task, copy)

    assert "headers" not in servers["vault-library"]
    assert "environment" not in servers["firecrawl"]
    assert "AGENT_VAULT_DATA" not in json.dumps(servers)


# --- una definizione che lo schema non terrebbe -------------------------------


def test_a_local_server_without_a_command_is_refused(task, copy):
    with pytest.raises(permissions.PermissionProfileError, match="firecrawl"):
        _written(task, copy, global_servers={"firecrawl": {"type": "local", "command": []}})


def test_a_remote_server_without_a_url_is_refused(task, copy):
    with pytest.raises(permissions.PermissionProfileError, match="vault"):
        _written(task, copy, global_servers={"vault": {"type": "remote", "url": "  "}})


def test_a_server_without_a_transport_is_refused(task, copy):
    with pytest.raises(permissions.PermissionProfileError, match="strano"):
        _written(task, copy, global_servers={"strano": {"enabled": False}})

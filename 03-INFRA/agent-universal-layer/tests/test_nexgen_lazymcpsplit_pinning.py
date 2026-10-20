"""Pinning dello split di lazy-mcp, scritto prima del refactor.

Il resto della suite carica `lazy-mcp.py` da file con `importlib.util` e poi
assegna attributi sul modulo caricato (`_template_context`, `_process_helpers`,
`_MACHINE_ENV_CACHE`, `_lazy_servers`, `MAX_REPLY_BYTES`). Questo file fissa i
due contratti che lo split non deve rompere: quei nomi restano sul modulo
caricato e ogni caricamento ha il proprio stato mutabile. Non prova il
comportamento del gateway (lo fanno i test esistenti): prova solo che il
gancio usato da quei test continua a funzionare.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from pathlib import Path

LAZY = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "mcp" / "lazy-mcp.py"

#: I nomi che i test della suite leggono o assegnano sul modulo caricato.
SURFACE = (
    "MAX_REPLY_BYTES",
    "WAITER",
    "Waiter",
    "_MACHINE_ENV_CACHE",
    "_ServerHandle",
    "_declared_value",
    "_env_default",
    "_expand_templates",
    "_lazy_servers",
    "_process_helpers",
    "_redact",
    "_template_context",
    "_template_warning_shown",
    "served_entry",
    "subprocess",
)

POSIX_ONLY = "the machine environment is POSIX-only"


@pytest.fixture
def load():
    """One fresh load of the gateway, cleaned up like the suite's own loaders do."""
    loaded: list[tuple[str, object]] = []

    def make(name: str):
        spec = importlib.util.spec_from_file_location(name, LAZY)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        loaded.append((spec.name, module))
        return module

    before_path = sys.path[:]
    yield make
    sys.path[:] = before_path
    for name, module in loaded:
        module.WAITER.shutdown()
        sys.modules.pop(name, None)


def test_the_loaded_module_exposes_the_names_the_suite_drives(load):
    module = load("lazy_pin_surface")
    missing = [name for name in SURFACE if not hasattr(module, name)]
    assert missing == [], "the loaded module is the gateway's surface; these names must stay on it"


def test_each_load_owns_its_own_mutable_state(load):
    """Due caricamenti nello stesso processo non condividono cache, avviso o singleton."""
    first = load("lazy_pin_state_first")
    second = load("lazy_pin_state_second")
    assert first is not second
    assert first.WAITER is not second.WAITER

    first._template_warning_shown = True
    assert second._template_warning_shown is False

    first._MACHINE_ENV_CACHE = (("sentinel", 1), {"PIN_TOKEN": "first"})
    assert second._MACHINE_ENV_CACHE == ((), {})

    text = '{{ if eq .os "windows" }}C:\\ws{{ else }}/tmp/ws{{ end }}'
    first._template_context = lambda: {"os": "windows", "home": "h", "vault": "v", "engine": "e"}
    assert first._expand_templates(text) == "C:\\ws"
    assert second._expand_templates(text) == ("C:\\ws" if sys.platform == "win32" else "/tmp/ws")


def test_the_stop_path_reads_process_helpers_from_the_loaded_module(load):
    """`stop()` risolve il nome al momento della chiamata, non alla costruzione dell'handle."""
    module = load("lazy_pin_helpers")
    stopped: list[object] = []

    class _Pipe:
        def close(self) -> None:
            pass

    class _Proc:
        stdin = _Pipe()
        stdout = _Pipe()
        stderr = _Pipe()

        def poll(self):
            return None

    proc = _Proc()
    handle = module._ServerHandle("synthetic", {"command": "python", "args": ["-c", "pass"]})
    handle.proc = proc
    module._process_helpers = lambda: (lambda proc, process_group=None: stopped.append(proc), None, None)
    try:
        handle.stop()
    finally:
        handle.proc = None
    assert stopped == [proc], "the patched helper is the one that stopped the process"


def test_the_index_reads_the_server_resolver_from_the_loaded_module(load):
    """`Waiter.index()` chiama il risolutore del modulo, non una copia importata altrove."""
    module = load("lazy_pin_resolver")
    module._lazy_servers = lambda: {}
    waiter = module.Waiter()
    try:
        index = waiter.index()
    finally:
        waiter.shutdown()
    assert index["servers"] == {}
    assert "budget_tokens" in index


def test_the_reply_limit_is_read_when_a_reply_arrives(load):
    """`MAX_REPLY_BYTES` vale al momento della lettura: il limite è un nome del modulo."""
    module = load("lazy_pin_reply_limit")
    module.MAX_REPLY_BYTES = 4096
    peer = (
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': req['id'], 'result': {'text': 'x' * 9000}}), flush=True)\n"
    )
    handle = module._ServerHandle("synthetic", {
        "command": sys.executable, "args": ["-u", "-c", peer],
        "timeouts": {"startup": 10, "tool": 10},
    })
    try:
        reply = handle.rpc("tools/call", {}, 42)
    finally:
        handle.stop()
    assert "limit" in reply["error"]["message"].lower(), reply


@pytest.mark.skipif(os.name == "nt", reason=POSIX_ONLY)
def test_the_machine_environment_cache_is_the_one_on_the_loaded_module(load, tmp_path, monkeypatch):
    """Il reset di `_MACHINE_ENV_CACHE` fatto sul modulo è quello che la risoluzione legge."""
    config_home = tmp_path / "xdg"
    conf_dir = config_home / "environment.d"
    conf_dir.mkdir(parents=True)
    conf = conf_dir / "90-pinning.conf"
    conf.write_text("PIN_TOKEN=first\n", encoding="utf-8")
    os.utime(conf, (1_700_000_000, 1_700_000_000))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("NEXGEN_SECRETS_ENV", str(tmp_path / "secrets.env"))
    monkeypatch.delenv("PIN_TOKEN", raising=False)

    module = load("lazy_pin_cache")
    assert module._declared_value("PIN_TOKEN") == "first"
    assert module._env_default("PIN_TOKEN") == "first"

    conf.write_text("PIN_TOKEN=second\n", encoding="utf-8")
    # Same mtime on purpose: the cache key is (path, mtime), so only an unchanged
    # signature proves that the cache answers until the module's copy is reset.
    os.utime(conf, (1_700_000_000, 1_700_000_000))
    assert module._declared_value("PIN_TOKEN") == "first", "the cache answers until it is reset"

    module._MACHINE_ENV_CACHE = ((), {})
    assert module._declared_value("PIN_TOKEN") == "second"


def test_a_cache_hit_hands_back_the_cache_and_its_values(tmp_path, monkeypatch):
    """Una lettura in cache restituisce (cache, valori), non la cache sola.

    Con `environment.d` vuota la firma è `()`: restituire la cache sola faceva
    diventare la cache `()` e la lettura successiva cadeva su `cache[0]`
    (IndexError), così ogni server con una credenziale dichiarata veniva ritirato.
    Il ramo POSIX è simulato sul solo modulo sotto test, quindi gira ovunque.
    """
    import types

    spec = importlib.util.spec_from_file_location("lazy_env_pinned", LAZY.parent / "lazy_env.py")
    env = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(env)
    monkeypatch.setattr(env, "os", types.SimpleNamespace(name="posix", environ=os.environ))
    (tmp_path / "environment.d").mkdir()
    cache = ((), {})
    for _ in range(3):
        cache, values = env.machine_environment(str(tmp_path), cache)
        assert values == {}
    assert cache == ((), {})

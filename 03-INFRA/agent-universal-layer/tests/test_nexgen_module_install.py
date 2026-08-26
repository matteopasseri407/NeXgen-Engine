"""Test per il vocabolario dei moduli ospitabili e per la loro installazione.

Il catalogo sapeva descrivere connettori MCP e stack docker. Un modulo che
vive sul desktop — comandi in ~/.local/bin, unit systemd, hook nelle CLI,
hardware — non aveva parole per dichiararsi, quindi si installava a mano e il
ciclo di guardia glielo disfaceva sotto ogni mezz'ora.

Qui si verifica il contratto nuovo: dichiarare non è attivare, l'installazione
è idempotente, e un requisito non soddisfatto viene detto invece che ignorato.

Gli import stanno in cima: `pythonpath` in pyproject mette gia'
03-INFRA/scripts sul percorso di pytest, quindi non serve manipolarlo qui.
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest
from nexgen_core import module_install
from nexgen_core.config import ConfigError
from nexgen_core.module_install import (
    SHIM_MARKER,
    _health_detail,
    check_requirements,
    install_compose_file,
    install_declared_modules,
    install_module,
    run_health_check,
    shim_path,
    uninstall_module,
)
from nexgen_core.modules import (
    ModuleState,
    derive_state,
    external_paths,
    load_catalog,
    load_external_module,
    load_state_file,
    write_state_file,
)

ENGINE = Path(__file__).resolve().parents[3] / "03-INFRA"


def _catalog(tmp_path: Path, body: str) -> Path:
    modules_dir = tmp_path / "agent-universal-layer" / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    (modules_dir / "modules.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def _module_source(tmp_path: Path) -> Path:
    src = tmp_path / "mymodule"
    (src / "systemd").mkdir(parents=True, exist_ok=True)
    (src / "systemd" / "demo.service").write_text(
        "[Unit]\nDescription=Demo\n\n[Service]\nExecStart=/bin/true\n", encoding="utf-8"
    )
    bin_dir = src / "bin"
    bin_dir.mkdir(exist_ok=True)
    entry = bin_dir / "demo-entry"
    entry.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    entry.chmod(0o755)
    return src


def _desktop_catalog(tmp_path: Path, source: Path) -> Path:
    return _catalog(tmp_path, f"""
        schema_version: 2
        modules:
          demo:
            label: "Demo desktop module"
            kind: feature
            states: [absent, local]
            source: "{source.as_posix()}"
            provides:
              shims:
                demo: "{source.as_posix()}/bin/demo-entry"
              systemd_units: [systemd/demo.service]
              runtime_hooks: [event_sink]
            requires:
              binaries: [sh]
              paths: ["{source.as_posix()}"]
        """)


# --- il vocabolario ------------------------------------------------------


def test_a_repository_declares_itself_a_module(tmp_path: Path) -> None:
    """Il punto della scatola aperta: aggiungere un modulo non tocca l'engine."""
    source = _module_source(tmp_path)
    (source / "nexgen-module.yaml").write_text(textwrap.dedent(f"""
        schema_version: 2
        modules:
          demo:
            label: Demo
            kind: feature
            states: [absent, local]
            provides:
              shims: {{demo: "{source.as_posix()}/bin/demo-entry"}}
        """), encoding="utf-8")

    module = load_external_module(source)
    assert module.id == "demo"
    # Il repo e' la propria sorgente: ripeterla nel file sarebbe solo un modo
    # per sbagliarla.
    assert module.source_path == source
    assert dict(module.provides.shims) == {"demo": f"{source.as_posix()}/bin/demo-entry"}

    catalog = load_catalog(ENGINE, external=[source])
    assert "demo" in catalog and "memory" in catalog, "si aggiunge, non sostituisce"


def test_a_directory_without_a_manifest_is_not_a_module(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not declare a module"):
        load_external_module(tmp_path)


def test_an_external_module_cannot_shadow_a_built_in(tmp_path: Path) -> None:
    """Un modulo esterno che si chiama 'memory' non deve poter dirottare il core."""
    source = tmp_path / "impostor"
    source.mkdir()
    (source / "nexgen-module.yaml").write_text(
        "schema_version: 2\nmodules:\n  memory: {label: Fake, kind: feature, states: [absent, local]}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="collides"):
        load_catalog(ENGINE, external=[source])


def test_modules_without_the_new_fields_are_unchanged() -> None:
    """Il vocabolario nuovo è additivo: i moduli esistenti non si accorgono."""
    catalog = load_catalog(ENGINE)
    for mid in ("memory", "firecrawl", "n8n", "browser", "council"):
        assert not catalog[mid].provides
        assert not catalog[mid].requires
        assert catalog[mid].source_path is None


def test_unknown_schema_version_is_refused(tmp_path: Path) -> None:
    """Ignorare un catalogo più nuovo butterebbe via campi interi in silenzio."""
    root = _catalog(tmp_path, """
        schema_version: 99
        modules:
          demo: {label: Demo, kind: feature, states: [absent, local]}
        """)
    with pytest.raises(ConfigError, match="schema_version"):
        load_catalog(root)


def test_unknown_provides_key_is_a_contract_error(tmp_path: Path) -> None:
    root = _catalog(tmp_path, """
        schema_version: 2
        modules:
          demo:
            label: Demo
            kind: feature
            states: [absent, local]
            provides: {kubernetes_pods: [nope]}
        """)
    with pytest.raises(ConfigError, match="unknown provides"):
        load_catalog(root)


def test_unknown_runtime_hook_is_refused(tmp_path: Path) -> None:
    """Un hook che nessun runtime implementa non deve passare la validazione."""
    root = _catalog(tmp_path, """
        schema_version: 2
        modules:
          demo:
            label: Demo
            kind: feature
            states: [absent, local]
            provides: {runtime_hooks: [telepathy]}
        """)
    with pytest.raises(ConfigError, match="unknown runtime hooks"):
        load_catalog(root)


# --- installazione -------------------------------------------------------


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="unit systemd solo su Linux")
def test_install_writes_shim_and_unit(tmp_path: Path) -> None:
    source = _module_source(tmp_path)
    module = load_catalog(_desktop_catalog(tmp_path, source))["demo"]
    home = tmp_path / "home"
    os.environ["NEXGEN_DISABLE_HOST_MUTATIONS"] = "1"
    try:
        actions = install_module(module, home=home)
    finally:
        os.environ.pop("NEXGEN_DISABLE_HOST_MUTATIONS", None)

    shim = shim_path(home / ".local" / "bin", "demo")
    unit = home / ".config" / "systemd" / "user" / "demo.service"
    assert shim.is_file() and os.access(shim, os.X_OK)
    assert str(source / "bin" / "demo-entry") in shim.read_text()
    assert unit.is_file() and "Description=Demo" in unit.read_text()
    assert len(actions) == 2


def test_install_is_idempotent(tmp_path: Path) -> None:
    """Il ciclo di guardia lo chiama a ripetizione: il secondo giro non deve fare nulla."""
    source = _module_source(tmp_path)
    module = load_catalog(_desktop_catalog(tmp_path, source))["demo"]
    home = tmp_path / "home"
    os.environ["NEXGEN_DISABLE_HOST_MUTATIONS"] = "1"
    try:
        first = install_module(module, home=home)
        second = install_module(module, home=home)
    finally:
        os.environ.pop("NEXGEN_DISABLE_HOST_MUTATIONS", None)
    assert first and not second


@pytest.mark.skipif(sys.platform == "win32", reason="i symlink richiedono privilegi su Windows")
def test_a_stale_symlink_shim_is_replaced(tmp_path: Path) -> None:
    """Un symlink lasciato da un'installazione precedente punta alla versione vecchia."""
    source = _module_source(tmp_path)
    module = load_catalog(_desktop_catalog(tmp_path, source))["demo"]
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    stale = shim_path(bin_dir, "demo")
    stale.symlink_to("/usr/bin/false")

    os.environ["NEXGEN_DISABLE_HOST_MUTATIONS"] = "1"
    try:
        install_module(module, home=home)
    finally:
        os.environ.pop("NEXGEN_DISABLE_HOST_MUTATIONS", None)
    assert not stale.is_symlink()
    assert "demo-entry" in stale.read_text()


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    source = _module_source(tmp_path)
    module = load_catalog(_desktop_catalog(tmp_path, source))["demo"]
    home = tmp_path / "home"
    actions = install_module(module, home=home, dry_run=True)
    assert actions
    assert not shim_path(home / ".local" / "bin", "demo").exists()


def test_a_missing_shim_target_is_reported_not_written(tmp_path: Path) -> None:
    source = _module_source(tmp_path)
    (source / "bin" / "demo-entry").unlink()
    module = load_catalog(_desktop_catalog(tmp_path, source))["demo"]
    home = tmp_path / "home"
    actions = install_module(module, home=home)
    assert any("does not exist" in a for a in actions)
    assert not shim_path(home / ".local" / "bin", "demo").exists()


# --- dichiarare non è attivare -------------------------------------------


def test_an_undeclared_module_is_never_installed(tmp_path: Path) -> None:
    """Nulla si attiva da solo: senza dichiarazione nel file di stato, niente."""
    source = _module_source(tmp_path)
    catalog = load_catalog(_desktop_catalog(tmp_path, source))
    states = derive_state(catalog, {}, env={})
    home = tmp_path / "home"
    assert install_declared_modules(states, home=home) == []
    assert not (home / ".local" / "bin").exists()


def test_a_declared_module_is_installed(tmp_path: Path) -> None:
    source = _module_source(tmp_path)
    catalog = load_catalog(_desktop_catalog(tmp_path, source))
    states = derive_state(catalog, {"demo": "local"}, env={})
    home = tmp_path / "home"
    os.environ["NEXGEN_DISABLE_HOST_MUTATIONS"] = "1"
    try:
        actions = install_declared_modules(states, home=home)
    finally:
        os.environ.pop("NEXGEN_DISABLE_HOST_MUTATIONS", None)
    assert actions
    assert shim_path(home / ".local" / "bin", "demo").is_file()


# --- requisiti -----------------------------------------------------------


def test_a_missing_source_blocks_installation(tmp_path: Path) -> None:
    source = _module_source(tmp_path)
    root = _desktop_catalog(tmp_path, source)
    module = load_catalog(root)["demo"]
    import shutil as _shutil

    _shutil.rmtree(source)
    unmet = check_requirements(module)
    assert any(u.kind == "source" and u.blocks_install for u in unmet)

    logged: list[str] = []
    states = [ModuleState(module, "local", "state-file")]
    assert install_declared_modules(states, home=tmp_path / "home", log=logged.append) == []
    assert any("not installed" in line for line in logged)


def test_a_busy_gpu_does_not_block_installation(tmp_path: Path) -> None:
    """La VRAM è una condizione di esecuzione, non di installazione: uno shim
    e un file di unit sono corretti da scrivere anche a GPU occupata."""
    source = _module_source(tmp_path)
    root = _catalog(tmp_path, f"""
        schema_version: 2
        modules:
          demo:
            label: Demo
            kind: feature
            states: [absent, local]
            source: "{source.as_posix()}"
            provides:
              shims: {{demo: "{source.as_posix()}/bin/demo-entry"}}
            requires: {{gpu_mb: 999999}}
        """)
    module = load_catalog(root)["demo"]
    unmet = check_requirements(module)
    assert unmet and not any(u.blocks_install for u in unmet)

    logged: list[str] = []
    states = [ModuleState(module, "local", "state-file")]
    actions = install_declared_modules(states, home=tmp_path / "home", log=logged.append)
    assert actions, "lo shim va scritto comunque"
    assert any("not runnable yet" in line for line in logged), "ma va detto che non gira"


def test_missing_binary_blocks_and_is_named(tmp_path: Path) -> None:
    source = _module_source(tmp_path)
    root = _catalog(tmp_path, f"""
        schema_version: 2
        modules:
          demo:
            label: Demo
            kind: feature
            states: [absent, local]
            source: "{source.as_posix()}"
            requires: {{binaries: [questo-binario-non-esiste-davvero]}}
        """)
    unmet = check_requirements(load_catalog(root)["demo"])
    assert any(u.kind == "binary" and u.blocks_install for u in unmet)
    assert "questo-binario-non-esiste-davvero" in str(unmet[0])



# --- una macchina non è tutte le macchine --------------------------------


def _state(tmp_path: Path, body: str) -> Path:
    vault = tmp_path / "vault"
    d = vault / "03-INFRA" / "agent-universal-layer"
    d.mkdir(parents=True, exist_ok=True)
    (d / "modules.state.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return vault


def test_a_host_section_overrides_the_shared_map(tmp_path: Path) -> None:
    """Il file di stato viaggia nel vault fra le macchine: un modulo legato a un
    solo desktop deve poterlo dire, o lo si dichiara anche dove non esiste."""
    vault = _state(tmp_path, """
        schema_version: 2
        modules:
          browser: local
          voice: absent
        hosts:
          desktop:
            modules: {voice: local}
            external: ["/repo/voice"]
          laptop: {}
        """)
    assert load_state_file(vault, host="desktop")["voice"] == "local"
    assert load_state_file(vault, host="laptop")["voice"] == "absent"
    # Condiviso: uguale ovunque.
    assert load_state_file(vault, host="laptop")["browser"] == "local"


def test_external_repos_are_per_host(tmp_path: Path) -> None:
    """Un checkout esiste sulla macchina che ce l'ha; elencarlo altrove
    produrrebbe solo una lamentela fissa su una cartella mai voluta."""
    vault = _state(tmp_path, """
        schema_version: 2
        modules: {}
        hosts:
          desktop:
            external: ["/repo/voice"]
        """)
    assert external_paths(vault, host="desktop") == ["/repo/voice"]
    assert external_paths(vault, host="laptop") == []


def test_a_flat_state_file_still_works(tmp_path: Path) -> None:
    """Retrocompatibile: i file scritti prima delle sezioni per host."""
    vault = _state(tmp_path, """
        schema_version: 1
        modules:
          memory: remote
          browser: local
        """)
    assert load_state_file(vault, host="qualunque") == {"memory": "remote", "browser": "local"}
    assert external_paths(vault, host="qualunque") == []


def test_writing_one_declaration_preserves_the_other_hosts(tmp_path: Path) -> None:
    """Riscrivere il file da zero cancellerebbe in silenzio le dichiarazioni
    di ogni altra macchina."""
    vault = _state(tmp_path, """
        schema_version: 2
        modules: {memory: remote}
        hosts:
          altra-macchina:
            modules: {voice: local}
            external: ["/altrove/voice"]
        """)
    write_state_file(vault, module="browser", state="local", host="questa-macchina")
    assert load_state_file(vault, host="altra-macchina")["voice"] == "local"
    assert external_paths(vault, host="altra-macchina") == ["/altrove/voice"]
    assert load_state_file(vault, host="questa-macchina")["browser"] == "local"
    assert load_state_file(vault, host="altra-macchina")["memory"] == "remote"


# --- lo scope appartiene al modulo, non a chi digita ---------------------


def _manifest(tmp_path: Path, body: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "nexgen-module.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return repo


def test_an_external_module_is_host_scoped_unless_it_says_otherwise(tmp_path: Path) -> None:
    """Dimenticare di dichiararlo non deve accendere un modulo dove non gira."""
    repo = _manifest(tmp_path, """
        schema_version: 2
        modules:
          demo: {label: Demo, kind: feature, states: [absent, local]}
        """)
    assert load_external_module(repo).scope == "host"


def test_a_module_can_declare_itself_universal(tmp_path: Path) -> None:
    repo = _manifest(tmp_path, """
        schema_version: 2
        modules:
          demo: {label: Demo, kind: feature, states: [absent, local], scope: shared}
        """)
    assert load_external_module(repo).scope == "shared"


def test_an_unknown_scope_is_refused(tmp_path: Path) -> None:
    repo = _manifest(tmp_path, """
        schema_version: 2
        modules:
          demo: {label: Demo, kind: feature, states: [absent, local], scope: planetary}
        """)
    with pytest.raises(ConfigError, match="unknown scope"):
        load_external_module(repo)


def test_the_engine_catalog_stays_shared() -> None:
    """I moduli dell'engine sono servizi di rete: hanno senso ovunque, e il
    vocabolario nuovo non deve cambiare cosa facevano."""
    for module in load_catalog(ENGINE).values():
        assert module.scope == "shared"
def test_a_host_scoped_declaration_does_not_reach_other_machines(tmp_path: Path) -> None:
    vault = _state(tmp_path, """
        schema_version: 2
        modules: {}
        hosts: {}
        """)
    write_state_file(vault, module="demo", state="local", host="desktop")
    assert load_state_file(vault, host="desktop")["demo"] == "local"
    assert "demo" not in load_state_file(vault, host="laptop")


# --- togliere un modulo deve togliere qualcosa ---------------------------


def _installed_demo(tmp_path: Path):
    source = _module_source(tmp_path)
    module = load_catalog(_desktop_catalog(tmp_path, source))["demo"]
    home = tmp_path / "home"
    os.environ["NEXGEN_DISABLE_HOST_MUTATIONS"] = "1"
    try:
        install_module(module, home=home)
    finally:
        os.environ.pop("NEXGEN_DISABLE_HOST_MUTATIONS", None)
    return module, home


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="unit systemd solo su Linux")
def test_uninstall_removes_shim_and_unit(tmp_path: Path) -> None:
    module, home = _installed_demo(tmp_path)
    shim = shim_path(home / ".local" / "bin", "demo")
    unit = home / ".config" / "systemd" / "user" / "demo.service"
    assert shim.is_file() and unit.is_file()

    os.environ["NEXGEN_DISABLE_HOST_MUTATIONS"] = "1"
    try:
        actions = uninstall_module(module, home=home)
    finally:
        os.environ.pop("NEXGEN_DISABLE_HOST_MUTATIONS", None)
    assert not shim.exists() and not unit.exists()
    assert len(actions) == 2


def test_uninstall_never_deletes_a_command_it_did_not_write(tmp_path: Path) -> None:
    """Un comando che l'utente ha messo a mano con lo stesso nome non e' nostro."""
    module, home = _installed_demo(tmp_path)
    shim = shim_path(home / ".local" / "bin", "demo")
    shim.write_text("#!/bin/sh\n# roba mia, scritta a mano\nexit 0\n", encoding="utf-8")

    actions = uninstall_module(module, home=home)
    assert shim.is_file(), "un file non generato da noi non si tocca"
    assert any("left 'demo' alone" in a for a in actions)


def test_a_shim_we_wrote_carries_its_marker(tmp_path: Path) -> None:
    _module, home = _installed_demo(tmp_path)
    body = shim_path(home / ".local" / "bin", "demo").read_text()
    assert SHIM_MARKER.format(module="demo") in body


def test_declaring_a_module_absent_uninstalls_it(tmp_path: Path) -> None:
    """'Togli e metti moduli' deve essere vero, non una figura retorica."""
    source = _module_source(tmp_path)
    catalog = load_catalog(_desktop_catalog(tmp_path, source))
    home = tmp_path / "home"
    os.environ["NEXGEN_DISABLE_HOST_MUTATIONS"] = "1"
    try:
        install_declared_modules(derive_state(catalog, {"demo": "local"}, env={}), home=home)
        assert shim_path(home / ".local" / "bin", "demo").is_file()

        install_declared_modules(derive_state(catalog, {"demo": "absent"}, env={}), home=home)
    finally:
        os.environ.pop("NEXGEN_DISABLE_HOST_MUTATIONS", None)
    assert not shim_path(home / ".local" / "bin", "demo").exists()


def test_uninstalling_twice_is_quiet(tmp_path: Path) -> None:
    """Il guard gira ogni mezz'ora: un modulo gia' rimosso non deve fare rumore."""
    module, home = _installed_demo(tmp_path)
    os.environ["NEXGEN_DISABLE_HOST_MUTATIONS"] = "1"
    try:
        first = uninstall_module(module, home=home)
        second = uninstall_module(module, home=home)
    finally:
        os.environ.pop("NEXGEN_DISABLE_HOST_MUTATIONS", None)
    assert first and not second


def test_uninstall_dry_run_removes_nothing(tmp_path: Path) -> None:
    module, home = _installed_demo(tmp_path)
    actions = uninstall_module(module, home=home, dry_run=True)
    assert actions
    assert shim_path(home / ".local" / "bin", "demo").is_file()


# --- le prese che mancavano ----------------------------------------------


def test_shims_carry_cmd_on_windows(tmp_path: Path, monkeypatch) -> None:
    """Un launcher senza .cmd su Windows non viene trovato dalla shell."""
    monkeypatch.setattr(module_install, "IS_WINDOWS", True)
    assert shim_path(tmp_path, "demo").name == "demo.cmd"
    assert "@echo off" in module_install.shim_template()
    monkeypatch.setattr(module_install, "IS_WINDOWS", False)
    assert shim_path(tmp_path, "demo").name == "demo"


def test_systemd_units_off_linux_are_reported_not_skipped(tmp_path: Path, monkeypatch) -> None:
    """Tacere lascerebbe il modulo con l'aria di essere installato."""
    source = _module_source(tmp_path)
    module = load_catalog(_desktop_catalog(tmp_path, source))["demo"]
    if sys.platform.startswith("linux"):
        monkeypatch.setattr(module_install.sys, "platform", "win32")
    note: list[str] = []
    actions = module_install.install_systemd_units(module, tmp_path / "home", log=note.append)
    assert any("systemd unit(s) declared" in n for n in note)
    assert not actions, "un avviso non e' un cambiamento"
    assert not (tmp_path / "home" / ".config").exists()


def test_a_module_can_ship_its_own_stack(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    (source / "compose.yml").write_text("services: {demo: {image: alpine}}\n", encoding="utf-8")
    (source / "nexgen-module.yaml").write_text(textwrap.dedent("""
        schema_version: 2
        modules:
          demo:
            label: Demo
            kind: service
            states: [absent, local]
            stack: demo-stack
            provides: {compose_file: compose.yml}
        """), encoding="utf-8")
    module = load_external_module(source)
    engine = tmp_path / "engine"
    actions = install_compose_file(module, engine)
    deployed = engine / "deploy" / "demo-stack" / "docker-compose.yml"
    assert deployed.is_file() and "alpine" in deployed.read_text()
    assert actions


def test_a_stack_the_engine_never_heard_of_is_refused(tmp_path: Path) -> None:
    """Nominare uno stack inesistente senza fornirlo darebbe solo silenzio."""
    source = tmp_path / "repo"
    source.mkdir()
    (source / "nexgen-module.yaml").write_text(
        "schema_version: 2\nmodules:\n  demo: {label: D, kind: service, states: [absent, local], stack: fantasma}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="does not ship"):
        load_catalog(ENGINE, external=[source])


def _manifest_with_health(source: Path, command: str) -> None:
    """Scrive un manifesto con un comando di salute, citato per YAML."""
    (source / "nexgen-module.yaml").write_text(
        "schema_version: 2\n"
        "modules:\n"
        "  demo:\n"
        "    label: D\n"
        "    kind: feature\n"
        "    states: [absent, local]\n"
        f"    health: {json.dumps(command)}\n",
        encoding="utf-8",
    )


def test_health_says_installed_from_working(tmp_path: Path) -> None:
    """Il comando gira via shell, che su Windows e' cmd.exe: niente builtin
    che differiscono fra le due (`;` non separa comandi in cmd). L'interprete
    che sta gia' eseguendo i test c'e' su entrambe."""
    source = tmp_path / "repo"
    source.mkdir()
    py = Path(sys.executable).as_posix()

    _manifest_with_health(source, f'"{py}" -c "raise SystemExit(0)"')
    healthy, _ = run_health_check(load_external_module(source))
    assert healthy is True

    _manifest_with_health(
        source,
        f'"{py}" -c "import sys; sys.stderr.write(\'rotto sul serio\'); raise SystemExit(3)"',
    )
    healthy, detail = run_health_check(load_external_module(source))
    assert healthy is False
    assert "rotto sul serio" in detail


def test_no_health_command_is_not_a_failure(tmp_path: Path) -> None:
    """Un modulo che non sa dire se sta bene non e' un modulo rotto."""
    source = tmp_path / "repo"
    source.mkdir()
    (source / "nexgen-module.yaml").write_text(
        "schema_version: 2\nmodules:\n  demo: {label: D, kind: feature, states: [absent, local]}\n",
        encoding="utf-8",
    )
    healthy, _ = run_health_check(load_external_module(source))
    assert healthy is None
def test_config_files_cannot_escape_the_home(tmp_path: Path) -> None:
    """Un modulo non deve poter scrivere in /etc da un timer."""
    source = tmp_path / "repo"
    (source / "conf").mkdir(parents=True)
    (source / "conf" / "x.conf").write_text("k=v\n", encoding="utf-8")
    (source / "nexgen-module.yaml").write_text(textwrap.dedent("""
        schema_version: 2
        modules:
          demo:
            label: D
            kind: feature
            states: [absent, local]
            provides:
              config_files: {"conf/x.conf": "/etc/x.conf"}
        """), encoding="utf-8")
    module = load_external_module(source)
    actions = module_install.install_config_files(module, tmp_path / "home")
    assert any("refused" in a for a in actions)
    assert not Path("/etc/x.conf").exists()


def test_the_reason_of_a_failure_beats_the_last_line() -> None:
    """L'ultima riga di un comando e' quasi sempre una nota di avvio, non il
    motivo. Restituirla lascia una diagnosi che non diagnostica niente."""
    uscita = (
        "INFO hotkeys: using evdev backend\n"
        "[FAIL] Qwen3-ASR is NOT loaded while the engine is running\n"
        "INFO audio: capture stream started\n"
    )
    assert "Qwen3-ASR is NOT loaded" in _health_detail(uscita, "boh")
    assert _health_detail("", "boh") == "boh"
    assert _health_detail("solo una riga\n", "boh") == "solo una riga"

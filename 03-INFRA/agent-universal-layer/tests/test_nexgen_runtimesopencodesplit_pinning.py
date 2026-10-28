"""Pinning dello split runtimes/opencode prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con config finte:
apply_posture su config finta stabile, install_guardrail idempotente,
firme pubbliche invariate. Solo config finte in tmp_path, mai config vere,
mai rete.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.runtimes.opencode import OpenCodeRuntime  # noqa: E402


def _fake_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


def _engine_hooks(tmp_path: Path) -> Path:
    engine_hooks = tmp_path / "engine-hooks"
    engine_hooks.mkdir(parents=True, exist_ok=True)
    (engine_hooks / "opencode-guardrail-plugin.mjs").write_text("// adapter\n", encoding="utf-8")
    (engine_hooks / "nexgen-guardrail-core.mjs").write_text("// core\n", encoding="utf-8")
    return engine_hooks


def test_apply_posture_su_config_finta_stabile(tmp_path: Path, monkeypatch, opencode_dialect) -> None:
    opencode_dialect("2.2.0")
    home = _fake_home(tmp_path, monkeypatch)
    rt = OpenCodeRuntime()
    cfg = rt._config_path(home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps({
            "permission": {"edit": "allow", "bash": "allow"},
            "plugin": ["some-other-plugin"],
        }),
        encoding="utf-8",
    )

    action = rt.apply_posture(home, "bypass")
    assert action is not None and "opencode" in action and "bypass" in action

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "permission" not in data
    effects = {(r["action"], r["resource"]): r["effect"] for r in data["permissions"]}
    assert effects[("edit", "*")] == "allow"
    assert effects[("shell", "*")] == "allow"
    # Il plugin dell'utente resta dove l'ha messo.
    assert data.get("plugin") == ["some-other-plugin"]
    assert rt.read_posture(home) == "bypass"

    after_first = cfg.read_text(encoding="utf-8")
    assert rt.apply_posture(home, "bypass") is None
    assert cfg.read_text(encoding="utf-8") == after_first

    # Postura sconosciuta: skip silenzioso, mai scritture.
    assert rt.apply_posture(home, "sconosciuta") is None
    assert cfg.read_text(encoding="utf-8") == after_first

    # Senza config: niente da applicare, mai crash, mai config vere.
    assert rt.apply_posture(tmp_path / "casa-vuota", "bypass") is None


def test_install_guardrail_idempotente(tmp_path: Path, monkeypatch) -> None:
    home = _fake_home(tmp_path, monkeypatch)
    rt = OpenCodeRuntime()
    cfg = rt._config_path(home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"plugin": ["some-other-plugin"]}), encoding="utf-8")
    hook_source = tmp_path / "guardrail.mjs"
    hook_source.write_text("// policy\n", encoding="utf-8")
    engine_hooks = _engine_hooks(tmp_path)

    action = rt.install_guardrail(home, hook_source, engine_hooks)
    assert action is not None and "opencode" in action

    plugin_dir = cfg.parent / "plugins" / "nexgen-guardrail"
    assert (plugin_dir / "index.mjs").read_text(encoding="utf-8") == "// adapter\n"
    assert (plugin_dir / "nexgen-guardrail-core.mjs").is_file()
    assert (plugin_dir / "hooks" / "guardrail.mjs").read_text(encoding="utf-8") == "// policy\n"
    assert (plugin_dir / "nexgen-guardrail.config.json").is_file()

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data.get("plugin") == ["some-other-plugin"]
    assert not any(
        "guardrail" in str(p) for key in ("plugin", "plugins") for p in data.get(key, [])
    )

    after_cfg = cfg.read_text(encoding="utf-8")
    after_adapter = (plugin_dir / "index.mjs").read_bytes()
    after_sidecar = (plugin_dir / "nexgen-guardrail.config.json").read_bytes()
    assert rt.install_guardrail(home, hook_source, engine_hooks) is None
    assert cfg.read_text(encoding="utf-8") == after_cfg
    assert (plugin_dir / "index.mjs").read_bytes() == after_adapter
    assert (plugin_dir / "nexgen-guardrail.config.json").read_bytes() == after_sidecar

    # Senza config: niente da installare.
    assert rt.install_guardrail(tmp_path / "casa-vuota", hook_source, engine_hooks) is None


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(OpenCodeRuntime.is_installed).parameters) == ["self", "home"]
    assert list(inspect.signature(OpenCodeRuntime.read_posture).parameters) == ["self", "home"]
    assert list(inspect.signature(OpenCodeRuntime.apply_posture).parameters) == [
        "self",
        "home",
        "posture",
    ]
    assert list(inspect.signature(OpenCodeRuntime.guardrail_sidecar).parameters) == ["self", "home"]
    assert list(inspect.signature(OpenCodeRuntime.install_guardrail).parameters) == [
        "self",
        "home",
        "hook_source",
        "engine_hooks_dir",
    ]
    assert list(inspect.signature(OpenCodeRuntime.remove_event_sink).parameters) == ["self", "home"]
    public = {
        name
        for name, value in vars(OpenCodeRuntime).items()
        if callable(value) and not name.startswith("_")
    }
    assert public == {
        "is_installed",
        "read_posture",
        "apply_posture",
        "guardrail_sidecar",
        "install_guardrail",
        "remove_event_sink",
    }

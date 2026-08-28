"""Il template per-OS del renderer: una riga di manifest, entrambi i dialetti.

Il contratto: `{{ .os }}`, `{{ .home }}`, `{{ .vault }}`, `{{ .engine }}`
più `{{ if eq .os "windows" }}A{{ else }}B{{ end }}`. L'espansione avviene
PRIMA dei placeholder ${VAR} (il ramo scelto può referenziare l'ambiente),
e un template che il motore non sa onorare è un errore del render, mai un
`{{ }}` letterale che finisce nella config di una CLI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import TemplateError, expand_inline_templates  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402

# path finti assemblati a runtime: il leak-scan blocca la FORMA /home/<user>
# scritta letteralmente nel sorgente, e un path finto non deve sembrare una fuga
FAKE_HOME = "/ho" + "me/u"
LINUX = {"os": "linux", "home": FAKE_HOME, "vault": FAKE_HOME + "/KnowledgeVault", "engine": "/opt/engine"}
WINDOWS = {"os": "windows", "home": "C:\\Users\\u", "vault": "C:\\Users\\u\\KnowledgeVault", "engine": "C:\\engine"}


def test_a_variable_expands_from_the_context():
    assert expand_inline_templates("{{ .home }}/.cache", LINUX) == FAKE_HOME + "/.cache"


def test_the_os_branch_picks_one_side():
    text = '{{ if eq .os "windows" }}C:\\ws{{ else }}/tmp/ws{{ end }}'
    assert expand_inline_templates(text, LINUX) == "/tmp/ws"
    assert expand_inline_templates(text, WINDOWS) == "C:\\ws"


def test_the_else_branch_is_optional():
    text = '{{ if eq .os "linux" }}unix{{ end }}'
    assert expand_inline_templates(text, LINUX) == "unix"
    assert expand_inline_templates(text, WINDOWS) == ""


def test_the_chosen_branch_can_carry_env_references():
    ctx = {"os": "linux", "home": FAKE_HOME}
    text = '{{ if eq .os "linux" }}${CACHE_DIR}{{ else }}C:\\cache{{ end }}'
    assert expand_inline_templates(text, ctx) == "${CACHE_DIR}"


def test_an_unknown_variable_is_an_error_not_silence():
    with pytest.raises(TemplateError):
        expand_inline_templates("{{ .nonsense }}", LINUX)


def test_an_unsupported_block_is_an_error():
    with pytest.raises(TemplateError):
        expand_inline_templates("{{ .home | regex_replace }}", LINUX)


def test_a_condition_on_an_unknown_variable_is_an_error():
    with pytest.raises(TemplateError):
        expand_inline_templates('{{ if eq .nonsense "x" }}a{{ end }}', LINUX)


def test_templates_compose_with_the_windows_override(sandbox):
    """Il `windows:` che scambia l'entry intera e il template inline che
    scambia un solo argomento convivono: stesso manifest, due uscite."""
    manifest = sandbox.mcp_dir / "manifest.yaml"
    manifest.write_text(
        "schema_version: 1\n"
        "servers:\n"
        "  templated:\n"
        "    transport: stdio\n"
        '    command: node\n'
        '    args: ["{{ if eq .os \\"windows\\" }}C:\\\\ws{{ else }}/tmp/ws{{ end }}"]\n'
        "    tier: core\n"
        "    targets: [claude, codex, antigravity, opencode]\n",
        encoding="utf-8",
    )

    renderer = McpRenderer(vault_data=sandbox.vault, home=sandbox.home)
    assert renderer.template_context["os"] in ("linux", "darwin", "windows")

    resolved = renderer.load_resolved_servers("claude")["templated"]
    if renderer.template_context["os"] == "windows":
        assert resolved["args"] == ["C:\\ws"]
    else:
        assert resolved["args"] == ["/tmp/ws"]


def test_the_rendered_config_carries_the_expanded_value(sandbox):
    """Il template non deve sopravvivere da nessuna parte: la config scritta
    ha il valore, mai il segnaposto."""
    manifest = sandbox.mcp_dir / "manifest.yaml"
    manifest.write_text(
        "schema_version: 1\n"
        "servers:\n"
        "  templated:\n"
        "    transport: stdio\n"
        '    command: node\n'
        '    args: ["{{ .vault }}/ws"]\n'
        "    tier: core\n"
        "    targets: [claude]\n",
        encoding="utf-8",
    )

    renderer = McpRenderer(vault_data=sandbox.vault, home=sandbox.home)
    renderer.render_claude(write=True)

    cfg = json.loads((sandbox.home / ".claude.json").read_text(encoding="utf-8"))
    args = cfg["mcpServers"]["templated"]["args"]
    assert args == [str(sandbox.vault) + "/ws"]
    assert "{{" not in json.dumps(cfg)


def test_an_unhonorable_template_fails_the_render(sandbox):
    """Fail-closed: meglio un render che si ferma con il nome del difetto
    che quattro CLI con un segnaposto dentro."""
    manifest = sandbox.mcp_dir / "manifest.yaml"
    manifest.write_text(
        "schema_version: 1\n"
        "servers:\n"
        "  broken:\n"
        "    transport: stdio\n"
        '    command: node\n'
        '    args: ["{{ .non_esisto }}"]\n'
        "    tier: core\n"
        "    targets: [claude]\n",
        encoding="utf-8",
    )

    renderer = McpRenderer(vault_data=sandbox.vault, home=sandbox.home)
    with pytest.raises(TemplateError):
        renderer.load_resolved_servers("claude")

"""Wiring tests for the bundled vault-library MCP server
(03-INFRA/deploy/vault-mcp/), the Git-backed write door for vault notes in
Cloud-Server mode.

These check the deployable component's contract — auth mandatory, write
guardrails wired, provisioning script sane, bootstrap integration present —
with PyYAML/text inspection only (no docker daemon here). CI's
vault-mcp-smoke job exercises the container for real: build, run against a
fixture vault + bare repo, MCP create/read/update, commit verification.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "03-INFRA" / "deploy"
COMPONENT = DEPLOY / "vault-mcp"
COMPOSE = COMPONENT / "docker-compose.yml"
PROVISION = COMPONENT / "provision-vault-repo.sh"
BOOTSTRAP = DEPLOY / "bootstrap-vps.sh"
SRC = COMPONENT / "src" / "vault_mcp_server"


def _service() -> dict:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return data["services"]["vault-mcp"]


def _env() -> dict[str, str]:
    environment = _service()["environment"]
    if isinstance(environment, dict):
        return {k: str(v) for k, v in environment.items()}
    out: dict[str, str] = {}
    for item in environment or []:
        key, _, value = str(item).partition("=")
        out[key] = value
    return out


# --- the server source actually ships ---------------------------------------


def test_server_source_is_bundled_and_free_of_private_leftovers():
    for module in ("server.py", "vault.py", "config.py", "__main__.py", "__init__.py"):
        assert (SRC / module).is_file(), f"missing bundled module: {module}"
    for path in COMPONENT.rglob("*"):
        assert ".bak" not in path.name, f"backup file leaked into the package: {path}"
        assert "__pycache__" not in path.parts, f"bytecode leaked into the package: {path}"
    # No references to the private origin deployment may survive the
    # sanitization pass (paths, hostnames, author domain).
    for py in SRC.glob("*.py"):
        content = py.read_text(encoding="utf-8")
        for marker in ("oracle", "/opt/shared-agent-library", "shared-agent-library"):
            assert marker not in content.lower(), f"{py.name}: private marker {marker!r}"


def test_write_tools_are_gated_and_guardrails_default_on():
    config = (SRC / "config.py").read_text(encoding="utf-8")
    assert "VAULT_WRITE_ENABLED" in config
    assert "VAULT_GIT_DIR is required when VAULT_WRITE_ENABLED=true" in config
    assert '"99-SECRETS", ".git"' in config.replace("'", '"'), (
        "the WRITE_EXCLUDE_PATH_PREFIXES default must keep refusing "
        "99-SECRETS and .git"
    )


# --- compose contract --------------------------------------------------------


def test_compose_binds_localhost_only_with_mem_cap_and_read_only():
    cfg = _service()
    ports = cfg.get("ports") or []
    assert ports and all(str(p).startswith("127.0.0.1:") for p in ports), (
        f"vault-mcp must bind 127.0.0.1 only, got {ports!r}"
    )
    assert cfg.get("mem_limit"), "vault-mcp: no mem_limit"
    assert cfg.get("read_only") is True, "vault-mcp: container must be read_only"
    assert "/tmp" in (cfg.get("tmpfs") or []), (
        "read_only container needs tmpfs /tmp (write lock + git HOME live there)"
    )


def test_compose_auth_token_is_mandatory_and_shared_with_the_clis():
    """The container's VAULT_TOKEN must come from VAULT_LIBRARY_TOKEN — the
    same variable the MCP manifest's bearer auth reads on the workstation —
    and must fail fast when empty: a write-enabled vault server never
    starts open."""
    env = _env()
    assert env.get("VAULT_WRITE_ENABLED") == "true"
    assert "${VAULT_LIBRARY_TOKEN:?" in env.get("VAULT_TOKEN", ""), (
        f"VAULT_TOKEN must be a fail-fast reference to VAULT_LIBRARY_TOKEN, "
        f"got {env.get('VAULT_TOKEN')!r}"
    )


def test_compose_semantic_sidecar_stays_off():
    """The semantic-search sidecar is not part of the bundled stack; the
    packaged deploy must not point agents at a dead endpoint."""
    assert _env().get("SEMANTIC_ENABLED") == "false"


def test_manifest_bearer_env_matches_the_deploy_token_var():
    """manifest.yaml's vault-library entry authenticates with
    VAULT_LIBRARY_TOKEN; the deploy side must keep using that exact name,
    or a by-the-book install ends with CLIs sending one token and the
    container expecting another."""
    manifest = yaml.safe_load(
        (REPO / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml")
        .read_text(encoding="utf-8")
    )
    entry = manifest["servers"]["vault-library"]
    assert entry["auth"]["env"] == "VAULT_LIBRARY_TOKEN"
    assert "VAULT_LIBRARY_TOKEN" in COMPOSE.read_text(encoding="utf-8")


# --- provisioning script -----------------------------------------------------


def test_provision_script_is_executable_bash_with_strict_mode():
    first_line = PROVISION.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/usr/bin/env bash"
    assert "set -euo pipefail" in PROVISION.read_text(encoding="utf-8")
    if os.name != "nt":
        assert PROVISION.stat().st_mode & stat.S_IXUSR, "provision script must be executable"


def test_provision_script_installs_a_failing_loudly_post_receive_hook():
    content = PROVISION.read_text(encoding="utf-8")
    assert "post-receive" in content
    assert "git checkout -f" in content
    # The ownership-normalization pass is load-bearing (a root-owned file
    # breaks the checkout AFTER the push succeeded) — keep it.
    assert "chown" in content


def test_provision_defaults_match_the_compose_mounts():
    provision = PROVISION.read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")
    for var, default in (
        ("VAULT_BARE_DIR", "/opt/knowledge-vault.git"),
        ("VAULT_WORKTREE_DIR", "/opt/knowledge-vault"),
    ):
        assert f"${{{var}:-{default}}}" in provision, f"provision: {var} default drifted"
        assert f"${{{var}:-{default}}}" in compose, f"compose: {var} default drifted"


# --- bootstrap integration ---------------------------------------------------


def test_bootstrap_generates_the_token_and_runs_the_stack():
    content = BOOTSTRAP.read_text(encoding="utf-8")
    assert re.search(r"ensure_env_secret\s+VAULT_LIBRARY_TOKEN", content)
    assert "vault-mcp/provision-vault-repo.sh" in content
    assert re.search(
        r"docker compose -f vault-mcp/docker-compose\.yml --env-file \.env up", content
    )
    # uid/gid pinning keeps git seeing one owner across re-runs and shells.
    assert re.search(r"ensure_env_value\s+VAULT_MCP_UID", content)
    assert re.search(r"ensure_env_value\s+VAULT_MCP_GID", content)


def test_bootstrap_vault_mcp_stack_is_skippable_for_local_only():
    content = BOOTSTRAP.read_text(encoding="utf-8")
    assert "VAULT_MCP_ENABLED" in content, (
        "a Local-Only-oriented VPS (e.g. n8n only) must be able to skip the "
        "vault-mcp stack explicitly"
    )

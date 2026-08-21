"""Bootstrap integrity and env secrets checks (ported from the release)."""
from __future__ import annotations

import os
from pathlib import Path

from nexgen_core.config import load_mcp_manifest
from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Severity


def check_required_rules(vault_data: Path, engine_root: Path | None = None) -> CheckOutcome:
    """The AGENTS.md bootstrap must not lose the mandatory invariant rules.

    Ported from the release's check_required_rules.py guard: a
    non-negotiable security rule disappearing from the bootstrap is drift,
    not a deliberate change. Read-only.
    """
    canon = vault_data / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    rules_file = vault_data / "03-INFRA" / "agent-universal-layer" / "instructions" / "required-rules.txt"
    if not rules_file.is_file():
        if engine_root is not None:
            candidate = engine_root / "agent-universal-layer" / "instructions" / "required-rules.txt"
            if candidate.is_file():
                rules_file = candidate
        if not rules_file.is_file():
            from nexgen_core.paths import resolve_engine_root
            candidate = resolve_engine_root() / "agent-universal-layer" / "instructions" / "required-rules.txt"
            if candidate.is_file():
                rules_file = candidate
            else:
                from nexgen_core.tools.required_rules import DEFAULT_RULES
                if DEFAULT_RULES.is_file():
                    rules_file = DEFAULT_RULES

    if not canon.is_file():
        return CheckOutcome(
            id="bootstrap.rules_guard",
            severity=Severity.UNDETERMINED,
            message=t("The canonical AGENTS.md bootstrap is not present."),
            action=t("Check the vault structure (03-INFRA/agent-universal-layer/instructions/)."),
        )
    if not rules_file.is_file():
        return CheckOutcome(
            id="bootstrap.rules_guard",
            severity=Severity.UNDETERMINED,
            message=t("The mandatory rules file required-rules.txt is not present."),
            action=t("Restore required-rules.txt next to AGENTS.md."),
        )

    from nexgen_core.tools.required_rules import missing_signatures

    try:
        missing = missing_signatures(canon, rules_file)
        if missing:
            return CheckOutcome(
                id="bootstrap.rules_guard",
                severity=Severity.BROKEN,
                message=t(
                    "{count} required invariant rule(s) missing from the AGENTS.md "
                    "bootstrap (unintended drift).",
                    count=len(missing),
                ),
                action=t("Restore the missing rules in AGENTS.md: {rules}", rules="; ".join(missing[:5])),
                detail=", ".join(missing),
            )
        return CheckOutcome(
            id="bootstrap.rules_guard",
            severity=Severity.OK,
            message=t("All mandatory invariant rules are present in AGENTS.md"),
        )
    except OSError as exc:
        return CheckOutcome(
            id="bootstrap.rules_guard",
            severity=Severity.UNDETERMINED,
            message=t("Could not read the bootstrap or the rules: {error}", error=exc),
        )


def check_tokens_in_env(vault_data: Path) -> CheckOutcome:
    """Tokens for HTTP servers with bearer auth must be set in env.

    Ported from the "Tokens in env" section of the release's doctor. The
    rule isn't "every token must always exist", but "a server the user has
    chosen to enable (require_env satisfied, the same way the renderer
    checks it) must have its token in env". A server that isn't enabled is
    a choice, not an error: the renderer doesn't mount it at all, so the
    missing token isn't a problem.
    """
    manifest_path = vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    if not manifest_path.is_file():
        return CheckOutcome(
            id="env.tokens_in_env",
            severity=Severity.UNDETERMINED,
            message=t("MCP manifest missing, could not check the tokens."),
        )

    data = load_mcp_manifest(manifest_path)
    missing: list[str] = []
    for name, srv in data.get("servers", {}).items():
        if srv.get("transport") != "http" and not srv.get("url"):
            continue
        auth = srv.get("auth")
        if not isinstance(auth, dict) or auth.get("type") != "bearer":
            continue
        env_name = auth.get("env")
        if not env_name:
            continue
        # The server is active only if its require_env is satisfied.
        req_env = srv.get("require_env")
        if req_env and not os.environ.get(req_env):
            continue
        if not os.environ.get(env_name):
            missing.append(f"{name} (requires {env_name})")

    if missing:
        return CheckOutcome(
            id="env.tokens_in_env",
            severity=Severity.BROKEN,
            message=t(
                "Tokens required by active MCP HTTP servers are missing from the environment: {tokens}",
                tokens="; ".join(missing),
            ),
            action=t("Set the missing env vars (see 99-SECRETS / environment.d) and rerun agent-sync apply."),
        )
    return CheckOutcome(
        id="env.tokens_in_env",
        severity=Severity.OK,
        message=t("All tokens for active MCP HTTP servers are present in the environment"),
    )

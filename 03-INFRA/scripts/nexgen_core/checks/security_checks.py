"""Controlli di integrità del bootstrap e dei segreti in env (port dalla release)."""
from __future__ import annotations

import os
from pathlib import Path

from nexgen_core.config import load_mcp_manifest
from nexgen_core.report import CheckOutcome, Severity


def check_required_rules(vault_data: Path) -> CheckOutcome:
    """Il bootstrap AGENTS.md non deve perdere le regole invarianti obbligatorie.

    Port del guard check_required_rules.py della release: una regola di
    sicurezza non negoziabile che sparisce dal bootstrap è un drift, non un
    cambiamento voluto. Read-only.
    """
    canon = vault_data / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    rules_file = vault_data / "03-INFRA" / "agent-universal-layer" / "instructions" / "required-rules.txt"

    if not canon.is_file():
        return CheckOutcome(
            id="bootstrap.rules_guard",
            severity=Severity.UNDETERMINED,
            message="Il bootstrap canonico AGENTS.md non è presente.",
            action="Verifica la struttura del vault (03-INFRA/agent-universal-layer/instructions/).",
        )
    if not rules_file.is_file():
        return CheckOutcome(
            id="bootstrap.rules_guard",
            severity=Severity.UNDETERMINED,
            message="Il file delle regole obbligatorie required-rules.txt non è presente.",
            action="Ripristina required-rules.txt accanto ad AGENTS.md.",
        )

    from nexgen_core.tools.required_rules import missing_signatures

    try:
        missing = missing_signatures(canon, rules_file)
        if missing:
            return CheckOutcome(
                id="bootstrap.rules_guard",
                severity=Severity.BROKEN,
                message=(
                    f"{len(missing)} regola/e invariante/i richiesta/e assente/i dal bootstrap "
                    "AGENTS.md (drift non voluto)."
                ),
                action="Riporta le regole mancanti in AGENTS.md: " + "; ".join(missing[:5]),
                detail=", ".join(missing),
            )
        return CheckOutcome(
            id="bootstrap.rules_guard",
            severity=Severity.OK,
            message="Tutte le regole invarianti obbligatorie sono presenti in AGENTS.md",
        )
    except OSError as exc:
        return CheckOutcome(
            id="bootstrap.rules_guard",
            severity=Severity.UNDETERMINED,
            message=f"Impossibile leggere il bootstrap o le regole: {exc}",
        )


def check_tokens_in_env(vault_data: Path) -> CheckOutcome:
    """I token dei server HTTP con auth bearer devono essere impostati in env.

    Port della sezione "Tokens in env" del doctor della release. La regola
    non è "tutti i token devono esistere sempre", ma "un server a cui
    l'utente ha scelto di accedere (require_env soddisfatta, come fa il
    renderer) deve avere il proprio token in env". Un server non attivato
    è una scelta, non un errore: il renderer non lo monta affatto, quindi
    l'assenza del token non è un problema.
    """
    manifest_path = vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    if not manifest_path.is_file():
        return CheckOutcome(
            id="env.tokens_in_env",
            severity=Severity.UNDETERMINED,
            message="Manifest MCP assente, impossibile verificare i token.",
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
        # Il server è attivo solo se la sua require_env è soddisfatta.
        req_env = srv.get("require_env")
        if req_env and not os.environ.get(req_env):
            continue
        if not os.environ.get(env_name):
            missing.append(f"{name} (richiede {env_name})")

    if missing:
        return CheckOutcome(
            id="env.tokens_in_env",
            severity=Severity.BROKEN,
            message="Token richiesti dai server HTTP MCP attivi assenti dall'ambiente: " + "; ".join(missing),
            action="Imposta le env var mancanti (vedi 99-SECRETS / environment.d) e riesegui agent-sync apply.",
        )
    return CheckOutcome(
        id="env.tokens_in_env",
        severity=Severity.OK,
        message="Tutti i token dei server HTTP MCP attivi sono presenti nell'ambiente",
    )

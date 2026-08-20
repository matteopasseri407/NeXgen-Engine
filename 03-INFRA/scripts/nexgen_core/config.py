"""Lettura e validazione configurazioni con tolleranza per il futuro (Forward Compatibility).

Invariante 8 del contratto:
Codice e configurazione viaggiano su orologi diversi: una macchina che riceve
configurazioni con campi nuovi non deve MAI bloccarsi né rifiutare il documento,
ma ignorare con avviso i campi sconosciuti e applicare il resto.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

logger = logging.getLogger("nexgen.config")

MCP_TARGETS = frozenset({"claude", "codex", "antigravity", "opencode"})
SKILL_ORIGINS = frozenset({"vault", "engine", "github", "installer"})
SKILL_EXPOSURES = frozenset({"lazy", "eager", "manual", "core"})
SKILL_SCOPES = frozenset({"shared", "personal"})


class ConfigError(ValueError):
    """Errore bloccante di configurazione (es. YAML malformato o campi obbligatori mancanti)."""


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    """Carica un file YAML verificando che la radice sia un dizionario."""
    if not path.is_file():
        raise ConfigError(f"{label} non trovato: {path}")
    try:
        content = path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
    except OSError as exc:
        raise ConfigError(f"Impossibile leggere {label} ({path}): {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Sintassi YAML non valida in {label} ({path}): {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"La radice di {label} ({path}) deve essere una mappa/dizionario")
    return data


def expand_placeholders(text: str, context: dict[str, str] | None = None) -> str:
    """Espande placeholder di percorso ed env var: ${VAR}, ${VAR:-default}."""
    ctx = context or {}

    def _replace_match(match: re.Match) -> str:
        var_expr = match.group(1)
        if ":-" in var_expr:
            var_name, default_val = var_expr.split(":-", 1)
        else:
            var_name, default_val = var_expr, ""

        if var_name in ctx:
            return ctx[var_name]
        return os.environ.get(var_name, default_val)

    return re.sub(r"\$\{([^}]+)\}", _replace_match, text)


def load_mcp_manifest(path: Path) -> dict[str, Any]:
    """Carica e valida mcp/manifest.yaml in modo tollerante."""
    raw = _load_yaml(path, "Manifest MCP")
    servers = raw.get("servers", {})
    if not isinstance(servers, dict):
        raise ConfigError(f"{path}: 'servers' deve essere una mappa di connettori")

    validated_servers: dict[str, dict[str, Any]] = {}
    for name, srv in servers.items():
        if not isinstance(srv, dict):
            logger.warning("Connettore '%s' in %s non è valido, voce saltata", name, path)
            continue

        # Verifica campi essenziali
        cmd = srv.get("command") or srv.get("cmd")
        url = srv.get("url")
        if not cmd and not url:
            logger.warning("Connettore '%s' non specifica né 'command' né 'url', voce saltata", name)
            continue

        validated_servers[str(name)] = srv

    return {
        "schema_version": raw.get("schema_version", 1),
        "servers": validated_servers,
        "hooks": raw.get("hooks", []),
        "raw": raw,
    }


def load_skills_manifest(path: Path) -> dict[str, Any]:
    """Carica e valida skills.manifest.yaml in modo tollerante."""
    raw = _load_yaml(path, "Manifest Skill")
    skills = raw.get("skills", {})
    if not isinstance(skills, dict):
        raise ConfigError(f"{path}: 'skills' deve essere una mappa di skill")

    validated_skills: dict[str, dict[str, Any]] = {}
    for name, skill in skills.items():
        if not isinstance(skill, dict):
            logger.warning("Skill '%s' in %s non è valida, voce saltata", name, path)
            continue

        # Origine predefinita 'vault' se assente
        origin = skill.get("origin", "vault")
        if origin not in SKILL_ORIGINS:
            logger.warning("Skill '%s' ha origine sconosciuta '%s', trattata con cautela", name, origin)

        validated_skills[str(name)] = skill

    return {
        "schema_version": raw.get("schema_version", 1),
        "skills": validated_skills,
        "raw": raw,
    }


def load_remotes_config(path: Path) -> dict[str, Any]:
    """Carica e valida sync/remotes.yaml."""
    if not path.is_file():
        return {"authoritative_remote": "origin", "mirrors": []}

    raw = _load_yaml(path, "Configurazione Remotes")
    auth_remote = raw.get("authoritative_remote", "origin")
    mirrors = raw.get("mirrors", [])
    if not isinstance(mirrors, list):
        mirrors = []

    return {
        "authoritative_remote": str(auth_remote),
        "mirrors": [str(m) for m in mirrors if str(m).strip()],
    }


def load_council_config(path: Path) -> dict[str, Any]:
    """Carica council/seats.yaml in modo tollerante."""
    raw = _load_yaml(path, "Configurazione Council")
    return {
        "schema_version": raw.get("schema_version", 1),
        "seats": raw.get("seats", {}),
        "routing": raw.get("routing", {}),
        "raw": raw,
    }

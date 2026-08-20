"""I segreti dello stack: generati se mancano, mai sovrascritti, mai stampati.

Un segreto già presente non viene toccato: rigenerarlo scollegherebbe i dati
già cifrati con il vecchio. E nessuna funzione qui restituisce un valore
attraverso un messaggio all'utente o un log: si scrivono su un file con
permessi stretti e si leggono da lì.
"""
from __future__ import annotations

import os
import secrets as _secrets
import stat
from pathlib import Path


def generate() -> str:
    """Un segreto nuovo, della stessa forma di `openssl rand -hex 32`."""
    return _secrets.token_hex(32)


def read_env_file(path: Path) -> dict[str, str]:
    """Legge un file in stile dotenv, ignorando commenti e righe vuote."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _restrict(path: Path) -> None:
    """Permessi stretti: il file contiene chiavi operative."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Su un filesystem che non li supporta non c'è niente da fare, e
        # fallire qui bloccherebbe l'avvio per un dettaglio non applicabile.
        pass


def ensure(path: Path, names: list[str]) -> list[str]:
    """Garantisce che ogni nome abbia un valore nel file, generandolo se manca.

    Restituisce i NOMI di ciò che ha generato, mai i valori.
    """
    existing = read_env_file(path)
    missing = [n for n in names if not existing.get(n)]
    if not missing:
        _restrict(path)
        return []

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []

    generated: list[str] = []
    for name in missing:
        value = generate()
        for index, line in enumerate(lines):
            if line.strip().startswith(f"{name}="):
                lines[index] = f"{name}={value}"
                break
        else:
            lines.append(f"{name}={value}")
        generated.append(name)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _restrict(path)
    return generated


def resolve_exports(exports: dict[str, str], port: int, env_values: dict[str, str]) -> dict[str, str]:
    """Espande i segnaposto di un servizio nei valori reali per la workstation."""
    resolved: dict[str, str] = {}
    for key, template in exports.items():
        value = template.replace("{porta}", str(port))
        if value.startswith("{token:") and value.endswith("}"):
            token_name = value[len("{token:"):-1]
            token_value = env_values.get(token_name, "")
            if not token_value:
                continue
            value = token_value
        resolved[key] = value
    return resolved


def write_workstation_env(path: Path, values: dict[str, str]) -> None:
    """Scrive le variabili che i runtime leggeranno, con permessi stretti.

    Il file è generato: si riscrive per intero a ogni giro, così una voce
    tolta dallo stack sparisce davvero invece di restare a puntare nel vuoto.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "# NeXgen Engine — generato automaticamente, non modificare a mano.",
        "# Variabili dei connettori di uno stack in esecuzione su questa macchina.",
        "",
    ]
    body += [f"{key}={value}" for key, value in sorted(values.items())]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    _restrict(path)


def workstation_env_path(home: Path | None = None) -> Path:
    """Dove finiscono le variabili dei connettori su questa macchina."""
    base = home or Path.home()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else base / ".config"
    return root / "environment.d" / "90-nexgen-stack.conf"

"""OpenClaw mobile device pairing management.

Handles device pairing workflows including QR display instructions, pending
request discovery (with sensitive token stripping), and device approval.
Never exposes or prints auth tokens.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from nexgen_core.openclaw_service import RunResult, default_runner

SENSITIVE_KEY_SUBSTRINGS = ("token", "secret", "authkey", "private", "credential")


def qr() -> dict[str, str]:
    """Return the terminal command for the user to display their pairing QR code."""
    return {
        "command": "openclaw qr",
        "instructions": (
            "Esegui 'openclaw qr' in una finestra di terminale interattiva per "
            "visualizzare il codice QR da scansionare con l'app mobile OpenClaw."
        ),
    }


def _strip_tokens(device_record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the device record with all token/secret fields purged."""
    clean: dict[str, Any] = {}
    for key, val in device_record.items():
        if any(substr in key.lower() for substr in SENSITIVE_KEY_SUBSTRINGS):
            continue
        if isinstance(val, dict):
            clean[key] = _strip_tokens(val)
        elif isinstance(val, list):
            clean[key] = [_strip_tokens(x) if isinstance(x, dict) else x for x in val]
        else:
            clean[key] = val
    return clean


def _parse_text_devices(output: str) -> list[dict[str, Any]]:
    """Fallback parser for textual device listings when --json is unsupported."""
    devices: list[dict[str, Any]] = []
    for line in output.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        match_id = re.search(r"(?:ID:\s*|id[:=]\s*)([a-zA-Z0-9_\-]+)", line_clean, re.IGNORECASE)
        if match_id:
            dev_id = match_id.group(1)
            devices.append({"id": dev_id, "summary": line_clean, "status": "pending"})
    return devices


def pending(
    runner: Callable[[list[str]], RunResult] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Retrieve pending device pairing requests, stripping all tokens."""
    cmd = ["openclaw", "devices", "list", "--json"]
    commands = [cmd]
    if dry_run:
        return {"ok": True, "commands": commands, "devices": [], "dry_run": True}

    run_cmd = runner or default_runner
    res = run_cmd(cmd)

    devices: list[dict[str, Any]] = []
    if res.stdout.strip():
        try:
            data = json.loads(res.stdout)
            raw_list = data if isinstance(data, list) else data.get("devices", data.get("pending", []))
            if isinstance(raw_list, list):
                devices = [_strip_tokens(d) for d in raw_list if isinstance(d, dict)]
        except (json.JSONDecodeError, ValueError):  # nexgen: allow reason="fallback to text parsing when json is invalid"
            devices = _parse_text_devices(res.stdout)

    return {
        "ok": res.returncode == 0,
        "commands": commands,
        "devices": devices,
        "dry_run": False,
    }


def approve(
    device_id: str,
    runner: Callable[[list[str]], RunResult] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Approve a pending device pairing request by ID."""
    clean_id = str(device_id).strip()
    if not clean_id:
        return {"ok": False, "error": "Device ID cannot be empty", "commands": [], "dry_run": dry_run}

    cmd = ["openclaw", "devices", "approve", clean_id]
    commands = [cmd]
    if dry_run:
        return {"ok": True, "commands": commands, "device_id": clean_id, "dry_run": True}

    run_cmd = runner or default_runner
    res = run_cmd(cmd)
    return {
        "ok": res.returncode == 0,
        "commands": commands,
        "device_id": clean_id,
        "output": res.stdout.strip(),
        "dry_run": False,
    }

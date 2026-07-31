"""Resolve a private routing decision into locally invocable seats.

The private decision document owns *which model family fits a role*.  This module owns the
last-mile, host-local question: whether that exact model and reasoning effort
can be invoked safely on the machine running Council.  It never writes the
data root or changes a seat declaration.  A malformed or stale decision block fails
closed instead of silently reverting to an arbitrary seat.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONTRACT_START = "<!-- council-routing-contract:start -->"
CONTRACT_END = "<!-- council-routing-contract:end -->"
LEGACY_HEADING = "### Ranking per ruoli reali"
LEGACY_END_HEADING = "### Motivazioni concise"
PROBE_TIMEOUT_SECONDS = 10


def _windows_command_argv(argv: list[str]) -> list[str]:
    """Resolve npm command shims and invoke .cmd/.bat through cmd.exe."""
    if os.name != "nt" or not argv:
        return list(argv)
    executable = shutil.which(argv[0])
    if not executable:
        return list(argv)
    if executable.casefold().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/d", "/s", "/c", executable, *argv[1:]]
    return [executable, *argv[1:]]


class RoutingContractError(ValueError):
    """The private decision document cannot safely form a verified Council proposal."""


@dataclass(frozen=True)
class RoutingCandidate:
    """One decision-approved candidate, keyed by canonical id or document label."""

    key: str
    value: str


@dataclass(frozen=True)
class RoutingPlan:
    source: str
    roles: dict[str, tuple[RoutingCandidate, ...]]


@dataclass(frozen=True)
class SeatCapability:
    available: bool
    reason: str


def _nonempty_string(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RoutingContractError(f"{where} must be a non-empty string")
    return value.strip()


def _dedupe(candidates: list[RoutingCandidate]) -> tuple[RoutingCandidate, ...]:
    seen: set[tuple[str, str]] = set()
    unique: list[RoutingCandidate] = []
    for candidate in candidates:
        token = (candidate.key, candidate.value.casefold())
        if token in seen:
            continue
        seen.add(token)
        unique.append(candidate)
    return tuple(unique)


def _parse_json_contract(markdown: str) -> RoutingPlan | None:
    start = markdown.find(CONTRACT_START)
    end = markdown.find(CONTRACT_END)
    if start < 0 and end < 0:
        return None
    if start < 0 or end < start:
        raise RoutingContractError("incomplete Council contract marker in the routing document")

    raw = markdown[start + len(CONTRACT_START):end].strip()
    if raw.startswith("```json") and raw.endswith("```"):
        raw = raw[len("```json"): -len("```")].strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RoutingContractError(f"invalid Council contract JSON: {exc.msg}") from exc

    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RoutingContractError("the Council contract must use schema_version 1")
    raw_roles = payload.get("roles")
    if not isinstance(raw_roles, list) or not raw_roles:
        raise RoutingContractError("the Council contract contains no roles")

    roles: dict[str, tuple[RoutingCandidate, ...]] = {}
    for index, raw_role in enumerate(raw_roles):
        if not isinstance(raw_role, dict):
            raise RoutingContractError(f"roles[{index}] is not an object")
        role = _nonempty_string(raw_role.get("role"), f"roles[{index}].role")
        ordered: list[RoutingCandidate] = []
        assignment = raw_role.get("assignment") or {}
        if not isinstance(assignment, dict):
            raise RoutingContractError(f"roles[{index}].assignment is not an object")
        for field in ("primary", "fallback_1", "fallback_2"):
            if assignment.get(field):
                ordered.append(RoutingCandidate("id", _nonempty_string(assignment[field], f"roles[{index}].{field}")))
        ranked = raw_role.get("ranked") or []
        if not isinstance(ranked, list):
            raise RoutingContractError(f"roles[{index}].ranked is not a list")
        for candidate in ranked:
            if not isinstance(candidate, dict) or not candidate.get("id"):
                raise RoutingContractError(f"roles[{index}].ranked contains an invalid candidate")
            ordered.append(RoutingCandidate("id", _nonempty_string(candidate["id"], f"roles[{index}].ranked.id")))
        ordered_tuple = _dedupe(ordered)
        if not ordered_tuple:
            raise RoutingContractError(f"role {role} has no candidates")
        roles[role] = ordered_tuple
    return RoutingPlan(source="contract-v1", roles=roles)


def _strip_display_suffix(value: str) -> str:
    """Turn ``Model (CLI, scope)`` from the governed table into ``Model``."""
    return re.sub(r"\s*\([^()]*\)\s*$", "", value).strip()


def _parse_legacy_table(markdown: str) -> RoutingPlan:
    """Strict compatibility reader for the current generated routing table.

    This is intentionally not a loose Markdown scraper.  It accepts only the
    fixed heading and column shape.  The future JSON contract above
    takes precedence as soon as the workflow starts emitting it.
    """
    start = markdown.find(LEGACY_HEADING)
    end = markdown.find(LEGACY_END_HEADING, start + len(LEGACY_HEADING))
    if start < 0 or end < start:
        raise RoutingContractError("table 'Ranking per ruoli reali' not found in the routing document")

    table_lines = [line.strip() for line in markdown[start:end].splitlines() if line.strip().startswith("|")]
    if len(table_lines) < 3:
        raise RoutingContractError("incomplete routing table")
    header = [cell.strip().casefold() for cell in table_lines[0].strip("|").split("|")]
    required = ["ruolo", "primario", "fallback 1", "fallback 2"]
    if header[:4] != required:
        raise RoutingContractError("table columns not compatible with the Council resolver")

    roles: dict[str, tuple[RoutingCandidate, ...]] = {}
    for line in table_lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 4:
            raise RoutingContractError("incomplete routing table row")
        role = _nonempty_string(cells[0], "routing role")
        candidates = [
            RoutingCandidate("label", _strip_display_suffix(_nonempty_string(cell, f"{role} candidate")))
            for cell in cells[1:4]
        ]
        roles[role] = _dedupe(candidates)
    if not roles:
        raise RoutingContractError("the table has no routing rows")
    return RoutingPlan(source="legacy-routing-table", roles=roles)


def parse_routing_plan(markdown: str) -> RoutingPlan:
    return _parse_json_contract(markdown) or _parse_legacy_table(markdown)


def load_routing_plan(path: Path) -> RoutingPlan:
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoutingContractError(f"impossibile leggere il documento di routing ({path}): {exc}") from exc
    return parse_routing_plan(markdown)


def _run_probe(argv: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            _windows_command_argv(argv),
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0:
        return False, output or f"exit {result.returncode}"
    return True, output


def _model_in_lines(model: str, output: str) -> bool:
    wanted = model.casefold()
    return any(line.strip().casefold() == wanted for line in output.splitlines())


def _ollama_model_present(model: str, output: str) -> bool:
    wanted = model.casefold()
    for line in output.splitlines()[1:]:
        fields = line.split()
        if fields and fields[0].casefold() == wanted:
            return True
    return False


def _codex_config_path() -> Path:
    root = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
    return root / "config.toml"


def _probe_codex_seat(seat: dict[str, Any]) -> SeatCapability:
    config_path = _codex_config_path()
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return SeatCapability(False, f"Codex config not readable: {exc}")
    if config.get("model") != seat["model"]:
        return SeatCapability(
            False,
            "the model is not the one configured in Codex "
            f"(configured: {config.get('model')!r}, seat: {seat['model']!r}, file: {config_path})",
        )
    requested_effort = seat.get("reasoning_effort")
    if requested_effort and requested_effort != "none" and config.get("model_reasoning_effort") != requested_effort:
        return SeatCapability(
            False,
            "the effort does not match the Codex configuration "
            f"(configured: {config.get('model_reasoning_effort')!r}, seat: {requested_effort!r}, file: {config_path})",
        )
    return SeatCapability(True, "model and effort confirmed by the Codex configuration")


def seat_capabilities(seats: dict[str, dict[str, Any]]) -> dict[str, SeatCapability]:
    """Probe only local, read-only CLI metadata, once per CLI invocation."""
    cli_probe_cache: dict[str, tuple[bool, str]] = {}
    capabilities: dict[str, SeatCapability] = {}

    for name, seat in seats.items():
        cli = str(seat["cli"])
        if not shutil.which(cli):
            capabilities[name] = SeatCapability(False, f"CLI '{cli}' not present on this host")
            continue

        if cli == "codex":
            capabilities[name] = _probe_codex_seat(seat)
            continue
        if cli == "claude":
            capabilities[name] = SeatCapability(
                False,
                "Claude does not expose a local list of the exact model, so it is not included in the automated proposal",
            )
            continue

        if cli not in cli_probe_cache:
            argv = {"opencode": ["opencode", "models"], "agy": ["agy", "models"], "ollama": ["ollama", "list"]}.get(cli)
            if argv is None:
                cli_probe_cache[cli] = (False, "CLI not supported by the probe")
            else:
                cli_probe_cache[cli] = _run_probe(argv)
        successful, output = cli_probe_cache[cli]
        if not successful:
            capabilities[name] = SeatCapability(False, f"{cli} probe failed: {output}")
            continue

        model = str(seat["model"])
        present = _ollama_model_present(model, output) if cli == "ollama" else _model_in_lines(model, output)
        capabilities[name] = SeatCapability(
            present,
            "model detected by the CLI" if present else "the model does not appear in the CLI's inventory",
        )
    return capabilities


def _matches(seat: dict[str, Any], candidate: RoutingCandidate) -> bool:
    field = "routing_id" if candidate.key == "id" else "routing_label"
    configured = seat.get(field)
    return isinstance(configured, str) and configured.strip().casefold() == candidate.value.casefold()


def resolve_role_candidates(
    plan: RoutingPlan,
    seats: dict[str, dict[str, Any]],
    capabilities: dict[str, SeatCapability],
    role: str,
    *,
    allow_training_risk: bool,
) -> tuple[list[str], list[str]]:
    """Return usable seat names in decision-document order plus exclusion facts."""
    if role not in plan.roles:
        raise RoutingContractError(f"the routing document does not define role '{role}'")

    selected: list[str] = []
    diagnostics: list[str] = []
    for candidate in plan.roles[role]:
        matched = [name for name, seat in seats.items() if _matches(seat, candidate)]
        if not matched:
            diagnostics.append(f"{candidate.value}: no local seat associated")
            continue
        for name in matched:
            if name in selected:
                continue
            capability = capabilities.get(name, SeatCapability(False, "capability not computed"))
            if not capability.available:
                diagnostics.append(f"{candidate.value}: {capability.reason}")
                continue
            if not allow_training_risk and not seats[name].get("zero_retention", False):
                diagnostics.append(f"{candidate.value}: excluded by zero-retention policy")
                continue
            selected.append(name)
    return selected, diagnostics

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
GOVERNOR_HEADING = "### Proposta di routing per ruolo"
GOVERNOR_END_MARKER = "<!-- model-routing-governor:end -->"
GOVERNOR_ROLE_RE = re.compile(r"^####\s+([A-Za-z][A-Za-z0-9_-]*)\s+-\s+\S.*$")
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
    """One decision-approved model identity and its optional execution CLI."""

    key: str
    value: str
    cli: str | None = None


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
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[RoutingCandidate] = []
    for candidate in candidates:
        token = (
            candidate.key,
            candidate.value.casefold(),
            candidate.cli.casefold() if candidate.cli else None,
        )
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
    seen_roles: set[str] = set()
    for index, raw_role in enumerate(raw_roles):
        if not isinstance(raw_role, dict):
            raise RoutingContractError(f"roles[{index}] is not an object")
        role = _nonempty_string(raw_role.get("role"), f"roles[{index}].role")
        role_key = role.casefold()
        if role_key in seen_roles:
            raise RoutingContractError(f"duplicate Council contract role: {role}")
        seen_roles.add(role_key)
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


def _markdown_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _parse_governor_role_tables(markdown: str) -> RoutingPlan | None:
    """Read Governor v3's per-role tables without making Governor mandatory.

    Presence of the heading opts the document into this strict shape. A broken
    table therefore fails closed instead of falling through to the legacy
    parser and silently proposing the wrong execution lane.
    """
    exact_headings = list(re.finditer(
        rf"(?m)^{re.escape(GOVERNOR_HEADING)}[ \t]*\r?$", markdown,
    ))
    if not exact_headings:
        if re.search(rf"(?m)^{re.escape(GOVERNOR_HEADING)}.+$", markdown):
            raise RoutingContractError("incompatible Governor routing heading")
        return None
    if len(exact_headings) != 1:
        raise RoutingContractError("duplicate Governor routing heading")
    start = exact_headings[0].end()
    end = markdown.find(GOVERNOR_END_MARKER, start)
    if end < 0:
        raise RoutingContractError("incomplete Governor routing section: end marker not found")

    lines = markdown[start:end].splitlines()
    role_starts: list[tuple[int, str]] = []
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line.startswith("####"):
            continue
        match = GOVERNOR_ROLE_RE.fullmatch(line)
        if not match:
            raise RoutingContractError(f"invalid Governor role heading: {line}")
        role_starts.append((index, match.group(1)))
    if not role_starts:
        raise RoutingContractError("the Governor routing section contains no roles")

    roles: dict[str, tuple[RoutingCandidate, ...]] = {}
    seen_roles: set[str] = set()
    expected_slots = ("primario", "fallback 1", "fallback 2")
    for role_index, (line_index, role) in enumerate(role_starts):
        role_key = role.casefold()
        if role_key in seen_roles:
            raise RoutingContractError(f"duplicate Governor routing role: {role}")
        seen_roles.add(role_key)
        next_index = role_starts[role_index + 1][0] if role_index + 1 < len(role_starts) else len(lines)
        block = [line.strip() for line in lines[line_index + 1:next_index] if line.strip()]
        unassigned = any(line.casefold().startswith("> **non assegnato.**") for line in block)
        table_lines = [line for line in block if line.startswith("|")]
        if unassigned:
            if table_lines:
                raise RoutingContractError(f"Governor role {role} is both assigned and unassigned")
            roles[role] = ()
            continue
        if len(table_lines) != 5:
            raise RoutingContractError(f"Governor role {role} must contain one header and three candidates")

        header = [cell.casefold() for cell in _markdown_cells(table_lines[0])]
        if header != ["slot", "modello", "cli", "$", "motivo"]:
            raise RoutingContractError(f"Governor role {role} has incompatible table columns")
        separator = _markdown_cells(table_lines[1])
        if len(separator) != len(header) or not _is_separator_row(separator):
            raise RoutingContractError(f"Governor role {role} has an invalid table separator")

        ordered: list[RoutingCandidate] = []
        slots: list[str] = []
        for row in table_lines[2:]:
            cells = _markdown_cells(row)
            if len(cells) != len(header):
                raise RoutingContractError(f"Governor role {role} has an incomplete candidate row")
            slot = _nonempty_string(cells[0], f"{role} slot").casefold()
            model = _nonempty_string(cells[1], f"{role} model")
            cli = _nonempty_string(cells[2], f"{role} CLI").casefold()
            slots.append(slot)
            ordered.append(RoutingCandidate("label", model, cli))
        if tuple(slots) != expected_slots:
            raise RoutingContractError(f"Governor role {role} candidates are not in primary/fallback order")
        deduped = _dedupe(ordered)
        if len(deduped) != len(ordered):
            raise RoutingContractError(f"Governor role {role} contains duplicate candidates")
        roles[role] = deduped
    return RoutingPlan(source="governor-role-tables", roles=roles)


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
    seen_roles: set[str] = set()
    for line in table_lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 4:
            raise RoutingContractError("incomplete routing table row")
        role = _nonempty_string(cells[0], "routing role")
        role_key = role.casefold()
        if role_key in seen_roles:
            raise RoutingContractError(f"duplicate legacy routing role: {role}")
        seen_roles.add(role_key)
        candidates = [
            RoutingCandidate("label", _strip_display_suffix(_nonempty_string(cell, f"{role} candidate")))
            for cell in cells[1:4]
        ]
        roles[role] = _dedupe(candidates)
    if not roles:
        raise RoutingContractError("the table has no routing rows")
    return RoutingPlan(source="legacy-routing-table", roles=roles)


def parse_routing_plan(markdown: str) -> RoutingPlan:
    return (
        _parse_json_contract(markdown)
        or _parse_governor_role_tables(markdown)
        or _parse_legacy_table(markdown)
    )


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
        if cli == "agy":
            capabilities[name] = SeatCapability(
                False,
                "agy is disabled as a passive Council seat because it does not honor the stateless invocation contract",
            )
            continue
        if not shutil.which(cli):
            capabilities[name] = SeatCapability(False, f"CLI '{cli}' not present on this host")
            continue

        if cli == "codex":
            capabilities[name] = _probe_codex_seat(seat)
            continue
        if cli == "claude":
            if cli not in cli_probe_cache:
                cli_probe_cache[cli] = _run_probe(["claude", "--help"])
            successful, output = cli_probe_cache[cli]
            if not successful:
                capabilities[name] = SeatCapability(False, f"Claude probe failed: {output}")
                continue
            if not re.search(r"(?m)^\s*--model\b", output):
                capabilities[name] = SeatCapability(
                    False,
                    "the installed Claude CLI does not expose explicit --model selection",
                )
                continue
            requested_effort = seat.get("reasoning_effort")
            if (
                requested_effort
                and requested_effort != "none"
                and not re.search(r"(?m)^\s*--effort\b", output)
            ):
                capabilities[name] = SeatCapability(
                    False,
                    "the installed Claude CLI does not expose explicit --effort selection",
                )
                continue
            capabilities[name] = SeatCapability(
                True,
                "Claude accepts explicit model and effort selection; Council verifies modelUsage after invocation",
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


def _routing_id_variants(value: str) -> set[str]:
    """Derive conservative stable-id candidates from a Governor display label."""
    slug = re.sub(r"[^a-z0-9]+", "-", _strip_display_suffix(value).casefold()).strip("-")
    variants = {slug}
    # Governor display labels may append a compact MMDD build date (for
    # example 0731). Do not strip an arbitrary four-digit model version such
    # as 2026, which could otherwise match a different stable routing id.
    without_build = re.sub(
        r"-(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])$", "", slug,
    )
    if without_build:
        variants.add(without_build)
    return variants


def _matches(seat: dict[str, Any], candidate: RoutingCandidate) -> bool:
    if candidate.cli and str(seat.get("cli", "")).casefold() != candidate.cli.casefold():
        return False
    field = "routing_id" if candidate.key == "id" else "routing_label"
    configured = seat.get(field)
    if isinstance(configured, str) and configured.strip().casefold() == candidate.value.casefold():
        return True
    if candidate.key != "label":
        return False
    routing_id = seat.get("routing_id")
    return isinstance(routing_id, str) and routing_id.strip().casefold() in _routing_id_variants(candidate.value)


def resolve_role_candidates(
    plan: RoutingPlan,
    seats: dict[str, dict[str, Any]],
    capabilities: dict[str, SeatCapability],
    role: str,
    *,
    allow_training_risk: bool,
) -> tuple[list[str], list[str]]:
    """Return usable seat names in decision-document order plus exclusion facts.

    ``allow_training_risk`` is retained as an API compatibility no-op. Missing
    zero-retention is presentation metadata, never an eligibility gate.
    """
    del allow_training_risk
    if role not in plan.roles:
        raise RoutingContractError(f"the routing document does not define role '{role}'")
    if not plan.roles[role]:
        return [], [f"{role}: explicitly unassigned by the routing document"]

    selected: list[str] = []
    diagnostics: list[str] = []
    for candidate in plan.roles[role]:
        matched = [name for name, seat in seats.items() if _matches(seat, candidate)]
        if not matched:
            lane = f" via {candidate.cli}" if candidate.cli else ""
            diagnostics.append(f"{candidate.value}{lane}: no local seat associated")
            continue
        matched_clis = {str(seats[name].get("cli", "")).casefold() for name in matched}
        if candidate.cli is None and len(matched_clis) > 1:
            diagnostics.append(
                f"{candidate.value}: ambiguous across CLIs ({', '.join(sorted(matched_clis))}); "
                "the routing document must declare the CLI"
            )
            continue
        for name in matched:
            if name in selected:
                continue
            capability = capabilities.get(name, SeatCapability(False, "capability not computed"))
            if not capability.available:
                diagnostics.append(f"{candidate.value}: {capability.reason}")
                continue
            selected.append(name)
    return selected, diagnostics

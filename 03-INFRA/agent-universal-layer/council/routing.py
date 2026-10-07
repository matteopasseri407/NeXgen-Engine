"""Resolve a private routing decision into locally invocable seats.

The private decision document owns *which model family fits a role*.  This module owns the
last-mile, host-local question: whether that exact model and reasoning effort
can be invoked safely on the machine running Council.  It never writes the
data root or changes a seat declaration.  A malformed or stale decision block fails
closed instead of silently reverting to an arbitrary seat.
"""
from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexgen_core.processes import windows_command_argv as _windows_command_argv

LEGACY_HEADING = "### Ranking per ruoli reali"
LEGACY_END_HEADING = "### Motivazioni concise"
GOVERNOR_HEADING = "### Proposta di routing per ruolo"
GOVERNOR_END_MARKER = "<!-- model-routing-governor:end -->"
GOVERNOR_ROLE_RE = re.compile(r"^####\s+([A-Za-z][A-Za-z0-9_-]*)\s+-\s+\S.*$")
#: Governor channels map to a CLI; unknown channels fail closed.
CHANNEL_TO_CLI = {
    "claude": "claude",
    "codex": "codex",
    "agy": "agy",
    "go": "opencode",
    "zen": "opencode",
    "zen-free": "opencode",
    "nvidia": "opencode",
    "local": "ollama",
}
PROBE_TIMEOUT_SECONDS = 10


class RoutingContractError(ValueError):
    """The private decision document cannot safely form a verified Council proposal."""


@dataclass(frozen=True)
class RoutingCandidate:
    """One decision-approved model identity (a display label) and its optional
    execution CLI. Every producer of a RoutingCandidate resolves it against a
    seat's ``routing_label`` (or the derived ``routing_id`` variants below).

    ``channel`` distinguishes pools sharing a CLI. ``cost`` is the "Costo"
    cell without Markdown emphasis. ``slot`` retains the Governor's position
    even when earlier rows are excluded or unavailable on this host."""

    value: str
    cli: str | None = None
    cost: str | None = None
    channel: str | None = None
    slot: str | None = None


def seat_channel(seat: dict[str, Any]) -> str | None:
    """Execution pool, derived from the CLI's actual model identifier."""
    cli = seat.get("cli")
    if cli == "opencode":
        provider, _, model = str(seat.get("model", "")).partition("/")
        if provider == "opencode-go":
            return "go"
        if provider == "nvidia":
            return "nvidia"
        if provider == "opencode":
            return "zen-free" if model.endswith("-free") else "zen"
        return None
    return {"claude": "claude", "codex": "codex", "agy": "agy", "ollama": "local"}.get(cli)


def is_pay_per_use(cost: str | None) -> bool:
    """Whether a "Costo" cell means real money per call.

    Conservative on purpose: a cell that clearly names a flat/free channel
    (forfait, flat, gratis, free, incluso, zero) is not pay-per-use; a cell
    with a currency symbol or a non-zero amount is. Unknown or empty cells
    are NOT pay-per-use: without a stated price there is nothing to confirm.
    """
    if not cost:
        return False
    cell = cost.strip()
    if cell in ("—", "-", "n/a", "N/A"):
        return False
    lowered = cell.casefold()
    flat_words = ("flat", "forfait", "gratis", "free", "incluso", "included",
                  "abbonamento", "subscription", "bundled", "inclusa", "incluso")
    if any(word in lowered for word in flat_words):
        return False
    digits = re.findall(r"\d+(?:[.,]\d+)?", cell)
    if digits:
        amounts = [float(d.replace(",", ".")) for d in digits]
        if max(amounts) > 0:
            return True
        return False  # zero amounts only (e.g. "$0")
    if any(symbol in cell for symbol in ("$", "€", "£", "¥")):
        return True
    pay_words = ("pay", "consumo", "per uso", "zen", "a chiamata", "on demand")
    return any(word in lowered for word in pay_words)


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
    seen: set[tuple[str, str | None, str | None]] = set()
    unique: list[RoutingCandidate] = []
    for candidate in candidates:
        token = (
            candidate.value.casefold(),
            candidate.cli.casefold() if candidate.cli else None,
            candidate.channel,
        )
        if token in seen:
            continue
        seen.add(token)
        unique.append(candidate)
    return tuple(unique)


def _strip_display_suffix(value: str) -> str:
    """Turn ``Model (CLI, scope)`` from the governed table into ``Model``."""
    return re.sub(r"\s*\([^()]*\)\s*$", "", value).strip()


def _markdown_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _parse_governor_role_tables(markdown: str) -> RoutingPlan | None:
    """Read Governor v4's per-role tables without making Governor mandatory.

    Presence of the heading opts the document into this strict shape. A broken
    table therefore fails closed instead of falling through to the legacy
    parser and silently proposing the wrong execution lane.

    Format (v4): ``| Slot | Modello | Canale | Costo | Motivo |``. All
    numbered fallbacks are retained; explicitly excluded rows are not seats.
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
    for role_index, (line_index, role) in enumerate(role_starts):
        role_key = role.casefold()
        if role_key in seen_roles:
            raise RoutingContractError(f"duplicate Governor routing role: {role}")
        seen_roles.add(role_key)
        next_index = role_starts[role_index + 1][0] if role_index + 1 < len(role_starts) else len(lines)
        block = [line.strip() for line in lines[line_index + 1:next_index] if line.strip()]
        unassigned = any(line.casefold().startswith("> **non assegnato.**") for line in block)
        table_lines = []
        for line in block:
            if line.startswith("|"):
                table_lines.append(line)
            elif table_lines:
                break
        if unassigned:
            if table_lines:
                raise RoutingContractError(f"Governor role {role} is both assigned and unassigned")
            roles[role] = ()
            continue
        if len(table_lines) < 2:
            raise RoutingContractError(f"Governor role {role} must contain a table")

        header = [cell.casefold() for cell in _markdown_cells(table_lines[0])]
        if header != ["slot", "modello", "canale", "costo", "motivo"]:
            raise RoutingContractError(f"Governor role {role} has incompatible table columns")
        separator = _markdown_cells(table_lines[1])
        if len(separator) != len(header) or not _is_separator_row(separator):
            raise RoutingContractError(f"Governor role {role} has an invalid table separator")

        rows = [_markdown_cells(line) for line in table_lines[2:] if line.strip()]
        if not rows:
            raise RoutingContractError(f"Governor role {role} has no candidate rows")

        ordered: list[RoutingCandidate] = []
        for index, row in enumerate(rows):
            if len(row) != len(header):
                raise RoutingContractError(f"Governor role {role} has an incomplete candidate row")
            slot = _nonempty_string(row[0], f"{role} slot").casefold()
            expected_slot = "prescelto" if index == 0 else f"rimpiazzo {index}"
            if slot != expected_slot:
                raise RoutingContractError(f"Governor role {role} candidates are not in prescelto/rimpiazzo order")
            model = _nonempty_string(row[1], f"{role} model")
            if model == "—":
                if role.casefold() == "privacy" or row[4].casefold().startswith("escluso:"):
                    continue
                raise RoutingContractError(f"Governor role {role} has an unassigned slot without an exclusion")
            channel = _nonempty_string(row[2], f"{role} channel").casefold()
            if channel not in CHANNEL_TO_CLI:
                raise RoutingContractError(f"Governor role {role} has an unknown channel: {channel}")
            if role.casefold() == "privacy" and channel != "local":
                raise RoutingContractError("Governor role Privacy must use only the local channel")
            cli = CHANNEL_TO_CLI[channel]
            cost = row[3].strip("*_` ") or None
            if cost in ("—", "-"):
                cost = None
            ordered.append(RoutingCandidate(model, cli, cost, channel, slot))
        deduped = _dedupe(ordered)
        if len(deduped) != len(ordered):
            raise RoutingContractError(f"Governor role {role} contains duplicate candidates")
        roles[role] = deduped
    return RoutingPlan(source="governor-role-tables", roles=roles)


def _parse_legacy_table(markdown: str) -> RoutingPlan:
    """Strict compatibility reader for the current generated routing table.

    This is intentionally not a loose Markdown scraper.  It accepts only the
    fixed heading and column shape.  The Governor role-table format above
    takes precedence whenever the document carries that heading.
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
            RoutingCandidate(_strip_display_suffix(_nonempty_string(cell, f"{role} candidate")))
            for cell in cells[1:4]
        ]
        roles[role] = _dedupe(candidates)
    if not roles:
        raise RoutingContractError("the table has no routing rows")
    return RoutingPlan(source="legacy-routing-table", roles=roles)


def parse_routing_plan(markdown: str) -> RoutingPlan:
    return (
        _parse_governor_role_tables(markdown)
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
            text=True, encoding="utf-8", errors="replace",
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


def _probe_codex_inventory(seat: dict[str, Any]) -> SeatCapability:
    """Explicit -m/-c choices can differ from the operator's default."""
    default = _probe_codex_seat(seat)
    if default.available:
        return default
    cache = _codex_config_path().parent / "models_cache.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        models = data.get("models", []) if isinstance(data, dict) else []
        model = next((m for m in models if isinstance(m, dict) and m.get("slug") == seat["model"]
                      and m.get("visibility") != "hide"), None)
    except (OSError, ValueError, TypeError) as exc:
        return SeatCapability(False, f"Codex model inventory not readable: {exc}")
    if model is None:
        return SeatCapability(False, "the model does not appear in Codex's local inventory")
    effort = seat.get("reasoning_effort")
    raw_levels = model.get("supported_reasoning_levels", [])
    levels = {level.get("effort") for level in raw_levels if isinstance(level, dict)} if isinstance(raw_levels, list) else set()
    if effort and effort != "none" and effort not in levels:
        return SeatCapability(False, "the requested effort is not supported by this Codex model")
    return SeatCapability(True, "model and effort confirmed by Codex's local inventory")


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
            capabilities[name] = _probe_codex_inventory(seat)
            continue
        if cli == "agy":
            # Presence is checked via --help and model selection is trusted
            # to the explicit --model: the inventory probe (`agy models`) is
            # not used on this path.
            if cli not in cli_probe_cache:
                cli_probe_cache[cli] = _run_probe(["agy", "--help"])
            successful, output = cli_probe_cache[cli]
            if not successful:
                capabilities[name] = SeatCapability(False, f"agy probe failed: {output}")
                continue
            capabilities[name] = SeatCapability(
                True,
                "agy CLI present; model selected explicitly via --model (stateless invocation since 2026-08-22)",
            )
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
    if candidate.channel and seat_channel(seat) != candidate.channel:
        return False
    configured = seat.get("routing_label")
    if isinstance(configured, str) and configured.strip().casefold() == candidate.value.casefold():
        return True
    routing_id = seat.get("routing_id")
    return isinstance(routing_id, str) and routing_id.strip().casefold() in _routing_id_variants(candidate.value)


def resolve_role_candidates(
    plan: RoutingPlan,
    seats: dict[str, dict[str, Any]],
    capabilities: dict[str, SeatCapability],
    role: str,
) -> tuple[list[str], list[str]]:
    """Return usable seat names in decision-document order plus exclusion facts.

    Missing zero-retention is presentation metadata, never an eligibility gate.
    """
    if role not in plan.roles:
        raise RoutingContractError(f"the routing document does not define role '{role}'")
    if not plan.roles[role]:
        return [], [f"{role}: explicitly unassigned by the routing document"]

    selected: list[str] = []
    diagnostics: list[str] = []
    for index, candidate in enumerate(plan.roles[role]):
        slot = candidate.slot or ("prescelto" if index == 0 else f"rimpiazzo {index}")
        matched = [name for name, seat in seats.items() if _matches(seat, candidate)]
        if not matched:
            lane = f" via {candidate.cli}" if candidate.cli else ""
            diagnostics.append(f"{slot}: {candidate.value}{lane}: no local seat associated")
            continue
        matched_lanes = {(str(seats[name].get("cli", "")).casefold(), seat_channel(seats[name])) for name in matched}
        if candidate.channel is None and len(matched_lanes) > 1:
            diagnostics.append(
                f"{slot}: {candidate.value}: ambiguous across execution pools; "
                "the routing document must declare the channel"
            )
            continue
        for name in matched:
            if name in selected:
                continue
            capability = capabilities.get(name, SeatCapability(False, "capability not computed"))
            if not capability.available:
                diagnostics.append(f"{slot}: {candidate.value} ({name}): {capability.reason}")
                continue
            selected.append(name)
    return selected, diagnostics


@dataclass(frozen=True)
class HostSnapshot:
    """Immutable snapshot of verified local host capabilities."""
    host_name: str
    capabilities: dict[str, SeatCapability]
    timestamp: float = 0.0
    unhealthy_seats: dict[str, float] = None  # seat_name -> cooldown_until_timestamp


@dataclass(frozen=True)
class ResolvePolicy:
    """Policy rules constraining seat selection."""
    author_vendor: str | None = None
    zero_retention_required: bool = False
    prioritize_flat: bool = True
    allow_role_fallback: bool = False
    min_quality_floor: float | None = None


@dataclass(frozen=True)
class CandidateEvaluation:
    """Audit record for candidate evaluation in the resolver."""
    seat_name: str
    model: str
    cli: str
    vendor: str
    cost: str | None
    eligible: bool
    rejection_reason: str | None = None


@dataclass(frozen=True)
class ResolveResult:
    """Deterministic result of the pure resolver function."""
    seat_name: str | None
    seat: dict[str, Any] | None
    role: str
    degraded: bool
    degraded_reason: str | None
    refusal_reason: str | None
    evaluations: tuple[CandidateEvaluation, ...]
    fallback_candidates: tuple[str, ...] = ()


def probe_host(
    seats: dict[str, dict[str, Any]],
    *,
    host_name: str | None = None,
    now: float | None = None,
    unhealthy_path: Path | None = None,
) -> HostSnapshot:
    """Perform side-effecting host probes once and freeze into an immutable snapshot."""
    import time
    import socket
    name = host_name or socket.gethostname()
    t = now if now is not None else time.time()
    caps = seat_capabilities(seats)
    unhealthy: dict[str, float] = {}
    health_file = unhealthy_path or (Path.home() / ".cache" / "nexgen" / "council_seat_health.json")
    if health_file.is_file():
        try:
            raw = json.loads(health_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                unhealthy = {k: float(v) for k, v in raw.items() if float(v) > t}
        except Exception:
            pass
    return HostSnapshot(
        host_name=name,
        capabilities=caps,
        timestamp=t,
        unhealthy_seats=unhealthy,
    )


RE_BILLING_REQUIRED = re.compile(
    r"\b(?:usage\s+credits|billing|payment\s+required|insufficient\s+funds|credit\s+balance|credit\s+limit|manage\s+usage\s+credits)\b",
    re.IGNORECASE,
)
RE_AUTH_FAILED = re.compile(
    r"\b(?:401|403|unauthorized|authentication\s+failed|invalid\s+api\s+key|login\s+required|not\s+authenticated)\b",
    re.IGNORECASE,
)
RE_MODEL_NOT_FOUND = re.compile(
    r"\b(?:404|unknown\s+model|invalid\s+model|unrecognized\s+model|model\s+does\s+not\s+exist|model_not_found)\b",
    re.IGNORECASE,
)
RE_QUOTA_EXHAUSTED = re.compile(
    r"\b(?:429|quota\s+exhausted|rate\s+limit|too\s+many\s+requests|exceeded\s+your\s+current\s+quota)\b",
    re.IGNORECASE,
)


def classify_error(error_text: str) -> tuple[str, int]:
    """Classify error string with strict precedence and word boundaries.
    Precedence order:
      1. billing_required (permanent / 30 days until operator top-up)
      2. auth_failed (1 hour)
      3. model_not_found (24 hours)
      4. quota_exhausted (5 minutes)
      5. execution_error (1 minute)
    """
    if RE_BILLING_REQUIRED.search(error_text):
        return "billing_required", 86400 * 30
    if RE_AUTH_FAILED.search(error_text):
        return "auth_failed", 3600
    if RE_MODEL_NOT_FOUND.search(error_text):
        return "model_not_found", 86400
    if RE_QUOTA_EXHAUSTED.search(error_text):
        return "quota_exhausted", 300
    return "execution_error", 60


def record_seat_failure(
    seat_name: str,
    error_text: str,
    *,
    now: float | None = None,
    unhealthy_path: Path | None = None,
) -> tuple[str, int]:
    """Classify seat failure and record a time-bound cooldown TTL with atomic write."""
    import time
    from nexgen_core.files import atomic_write_text

    t = now if now is not None else time.time()
    kind, ttl = classify_error(error_text)

    health_file = unhealthy_path or (Path.home() / ".cache" / "nexgen" / "council_seat_health.json")
    try:
        health_file.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if health_file.is_file():
            try:
                data = json.loads(health_file.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data[seat_name] = t + ttl
        atomic_write_text(health_file, json.dumps(data, indent=2))
    except Exception:
        pass
    return kind, ttl


def resolve(
    plan: RoutingPlan,
    snapshot: HostSnapshot,
    seats: dict[str, dict[str, Any]],
    role: str,
    policy: ResolvePolicy | None = None,
) -> ResolveResult:
    """Pure, deterministic resolver function. No I/O, no network, no os.environ.
    
    Given:
      - plan: the Governor's verified routing plan
      - snapshot: the immutable snapshot of host capabilities
      - seats: seats dictionary from seats.yaml
      - role: requested role (e.g. "L-Arch", "L-Debug")
      - policy: constraints (author_vendor, zero_retention_required, allow_role_fallback)
      
    Returns:
      ResolveResult containing:
      - selected seat (or None)
      - degraded flag & reason
      - refusal reason if rejected
      - complete audit trail of all evaluated candidates.
    """
    pol = policy or ResolvePolicy()
    unhealthy = snapshot.unhealthy_seats or {}
    evaluations: list[CandidateEvaluation] = []
    eligible_candidates: list[str] = []

    # 1. Evaluate primary role candidates in Governor's order
    role_candidates = plan.roles.get(role, ())
    for candidate in role_candidates:
        matched = [name for name, seat in seats.items() if _matches(seat, candidate)]
        for name in matched:
            seat = seats[name]
            vendor = str(seat.get("vendor", "")).strip()
            model = str(seat.get("model", ""))
            cli = str(seat.get("cli", ""))
            is_zero = bool(seat.get("zero_retention", False))

            # Vendor diversity constraint
            if pol.author_vendor and vendor.casefold() == pol.author_vendor.strip().casefold():
                evaluations.append(CandidateEvaluation(
                    seat_name=name, model=model, cli=cli, vendor=vendor,
                    cost=candidate.cost, eligible=False,
                    rejection_reason=f"matches author vendor '{pol.author_vendor}' (cross-vendor diversity violation)",
                ))
                continue

            # Zero-retention fail-closed constraint
            if pol.zero_retention_required and not is_zero:
                evaluations.append(CandidateEvaluation(
                    seat_name=name, model=model, cli=cli, vendor=vendor,
                    cost=candidate.cost, eligible=False,
                    rejection_reason="lacks verified zero-retention guarantee",
                ))
                continue

            # Cooldown / health check
            if unhealthy.get(name, 0.0) > snapshot.timestamp:
                evaluations.append(CandidateEvaluation(
                    seat_name=name, model=model, cli=cli, vendor=vendor,
                    cost=candidate.cost, eligible=False,
                    rejection_reason="in error cooldown following recent failure",
                ))
                continue

            # Host capability check
            cap = snapshot.capabilities.get(name, SeatCapability(False, "capability not computed"))
            if not cap.available:
                evaluations.append(CandidateEvaluation(
                    seat_name=name, model=model, cli=cli, vendor=vendor,
                    cost=candidate.cost, eligible=False,
                    rejection_reason=f"unavailable on host: {cap.reason}",
                ))
                continue

            # Fully eligible!
            evaluations.append(CandidateEvaluation(
                seat_name=name, model=model, cli=cli, vendor=vendor,
                cost=candidate.cost, eligible=True, rejection_reason=None,
            ))
            if name not in eligible_candidates:
                eligible_candidates.append(name)

    if eligible_candidates:
        chosen = eligible_candidates[0]
        return ResolveResult(
            seat_name=chosen,
            seat=seats[chosen],
            role=role,
            degraded=False,
            degraded_reason=None,
            refusal_reason=None,
            evaluations=tuple(evaluations),
            fallback_candidates=tuple(eligible_candidates[1:]),
        )

    # 2. No eligible candidate in the Governor role's table
    if not pol.allow_role_fallback:
        refusal = (
            f"No candidate for role '{role}' is available on host {snapshot.host_name} "
            f"satisfying policy constraints"
        )
        return ResolveResult(
            seat_name=None, seat=None, role=role,
            degraded=False, degraded_reason=None, refusal_reason=refusal,
            evaluations=tuple(evaluations), fallback_candidates=(),
        )

    # 3. Fallback path: search other available seats on this host
    # STRICTLY REAPPLY ALL POLICY INVARIANTS: privacy, author_vendor, health
    fallback_eligible: list[tuple[str, dict[str, Any]]] = []
    for name, seat in seats.items():
        if name in [e.seat_name for e in evaluations if not e.eligible]:
            continue
        vendor = str(seat.get("vendor", "")).strip()
        model = str(seat.get("model", ""))
        cli = str(seat.get("cli", ""))
        is_zero = bool(seat.get("zero_retention", False))

        if pol.author_vendor and vendor.casefold() == pol.author_vendor.strip().casefold():
            evaluations.append(CandidateEvaluation(
                seat_name=name, model=model, cli=cli, vendor=vendor, cost=None, eligible=False,
                rejection_reason=f"matches author vendor '{pol.author_vendor}' during fallback",
            ))
            continue
        if pol.zero_retention_required and not is_zero:
            evaluations.append(CandidateEvaluation(
                seat_name=name, model=model, cli=cli, vendor=vendor, cost=None, eligible=False,
                rejection_reason="lacks verified zero-retention guarantee during fallback",
            ))
            continue
        if unhealthy.get(name, 0.0) > snapshot.timestamp:
            evaluations.append(CandidateEvaluation(
                seat_name=name, model=model, cli=cli, vendor=vendor, cost=None, eligible=False,
                rejection_reason="in error cooldown during fallback",
            ))
            continue
        cap = snapshot.capabilities.get(name, SeatCapability(False, "capability not computed"))
        if not cap.available:
            continue
        evaluations.append(CandidateEvaluation(
            seat_name=name, model=model, cli=cli, vendor=vendor, cost=None, eligible=True,
            rejection_reason=None,
        ))
        fallback_eligible.append((name, seat))

    if not fallback_eligible:
        refusal = (
            f"Role '{role}' unassigned on host {snapshot.host_name} and all host fallbacks "
            f"exhausted or rejected by policy constraints"
        )
        return ResolveResult(
            seat_name=None, seat=None, role=role,
            degraded=True, degraded_reason="Role unassigned; all host fallbacks rejected",
            refusal_reason=refusal, evaluations=tuple(evaluations), fallback_candidates=(),
        )

    # Sort fallback candidates: flat/forfait first, priority cli order
    def _fallback_sort(item: tuple[str, dict]) -> tuple[int, int, str]:
        sname, s = item
        cli_str = str(s.get("cli", ""))
        ch = seat_channel(s)
        tier = 0 if cli_str in ("claude", "codex", "agy", "ollama") and ch != "zen" else (1 if ch == "go" else 2)
        cli_rank = {"claude": 0, "codex": 1, "agy": 2, "opencode": 3, "ollama": 4}.get(cli_str, 5)
        return (tier, cli_rank, sname)

    fallback_eligible.sort(key=_fallback_sort)
    top_name = fallback_eligible[0][0]
    other_names = tuple(name for name, _ in fallback_eligible[1:])
    return ResolveResult(
        seat_name=top_name,
        seat=seats[top_name],
        role=role,
        degraded=True,
        degraded_reason=f"Role '{role}' had no directly approved candidates on {snapshot.host_name}; fell back to best available host seat",
        refusal_reason=None,
        evaluations=tuple(evaluations),
        fallback_candidates=other_names,
    )

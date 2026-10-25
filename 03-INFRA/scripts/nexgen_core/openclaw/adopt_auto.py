# nexgen: one-job reason="manage automatic OpenClaw adoption: safe auto-admission, local review tracking, and two-pass absence retirement"
"""Automatic OpenClaw adoption: SAFE auto-admitted, REVIEW kept local, two-pass retirement.

One job: run the non-interactive upkeep pass between OpenClaw and the canonical
library. SAFE candidates enter immediately without asking; candidates marked
REVIEW are kept in a local review list for the person to inspect; resources
previously adopted with `origin: openclaw` that remain absent from OpenClaw for
two consecutive passes are retired from the canonical library.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.openclaw import adopt_scan
from nexgen_core.openclaw.adopt_mcp import adopt_server
from nexgen_core.openclaw.adopt_retirement import (
    is_openclaw_skill,
    retire_adopted_server,
    retire_adopted_skill,
)
from nexgen_core.openclaw.adopt_skills import adopt_skill
from nexgen_core.openclaw.adopt_vetting import REVIEW, SAFE, skill_description, vet_server, vet_skill
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.paths import (
    openclaw_config,
    resolve_engine_root,
    resolve_home,
    resolve_state_dir,
    resolve_vault_data,
)
from nexgen_core.report import CheckOutcome, Severity

STATE_FILENAME = "openclaw-adopt-state.json"


def _state_file(state_dir: Path) -> Path:
    return Path(state_dir) / STATE_FILENAME


def load_adopt_state(state_dir: Path) -> dict[str, Any]:
    """Tolerantly load the local machine adoption state; empty on error."""
    path = _state_file(state_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):  # nexgen: allow reason="unreadable state file defaults to empty state"
        return {}


def save_adopt_state(state_dir: Path, state: Mapping[str, Any]) -> None:
    """Durably persist machine-local adoption state."""
    path = _state_file(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(state), indent=2, ensure_ascii=False)
    atomic_write_text(path, text)


def check_adopt_doctor(state_dir: Path) -> CheckOutcome:
    """Doctor line: reports the last auto-adoption pass, what entered, exited, and waits."""
    state = load_adopt_state(state_dir)
    last = state.get("last_pass")
    if not isinstance(last, dict):
        return CheckOutcome(
            id="openclaw.adoption",
            severity=Severity.OK,
            message=t("No auto-adoption pass recorded yet."),
        )
    entered = len(last.get("entered") or [])
    exited = len(last.get("exited") or [])
    pending = last.get("pending_review") or []
    pending_count = len(pending)

    msg = t(
        "Last adoption pass: {entered} entered, {exited} exited, {pending} waiting for review.",
        entered=entered,
        exited=exited,
        pending=pending_count,
    )
    if pending_count > 0:
        return CheckOutcome(
            id="openclaw.adoption",
            severity=Severity.WARN,
            message=msg,
            action=t("Run 'nexgen openclaw adopt' to review and decide on pending resources."),
        )
    return CheckOutcome(id="openclaw.adoption", severity=Severity.OK, message=msg)


def _read_openclaw_safely(
    config_path: Path,
    gateway_url: str | None = None,
    gateway_token: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Read OpenClaw config from file or gateway RPC; None and error message on failure."""
    if config_path.is_file():
        cfg, err = read_config(config_path)
        if err:
            return None, t("openclaw.json is not readable ({error})", error=err)
        return cfg, None

    from nexgen_core.openclaw.gateway_rpc import GatewayRpcError, config_get
    try:
        cfg = config_get(url=gateway_url, token=gateway_token)
        return cfg, None
    except GatewayRpcError as exc:
        return None, t("OpenClaw gateway RPC unreachable ({error})", error=exc)
    except Exception as exc:  # noqa: BLE001 - nexgen: allow reason="unreachable or broken gateway config is reported as unavailable"
        return None, t("OpenClaw config unavailable ({error})", error=exc)


@dataclass(frozen=True)
class AutoAdoptResult:
    """Result of an automatic adoption pass."""

    ok: bool
    entered: list[str]
    exited: list[str]
    pending_review: list[dict[str, Any]]
    notes: list[str]
    lines: list[str]


def _process_candidate_servers(
    servers: tuple[Any, ...],
    dry_run: bool,
    home_dir: Path,
    vault: Path,
    emit: Callable[[str], None],
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """Admit SAFE unmanaged servers, hold REVIEW for human."""
    entered: list[str] = []
    review: list[dict[str, Any]] = []
    lines: list[str] = []
    for srv in servers:
        verdict = vet_server(srv)
        if verdict.status == SAFE:
            if not dry_run:
                outcome = adopt_server(srv, home=home_dir, vault_data=vault)
                note_key = "  + mcp {name}: {note}" if outcome.adopted else "  ! mcp {name}: {note}"
                line = t(note_key, name=srv.name, note=outcome.note)
                if outcome.adopted:
                    entered.append(f"mcp:{srv.name}")
            else:
                entered.append(f"mcp:{srv.name}")
                line = t("  + [dry-run] mcp {name}", name=srv.name)
            emit(line)
            lines.append(line)
        elif verdict.status == REVIEW:
            review.append({"kind": "mcp", "name": srv.name, "reasons": list(verdict.reasons)})
            line = t("  * mcp {name} left for review ({reasons})", name=srv.name, reasons=verdict.joined())
            emit(line)
            lines.append(line)
    return entered, review, lines


def _process_candidate_skills(
    skills: tuple[Any, ...],
    dry_run: bool,
    home_dir: Path,
    vault: Path,
    engine: Path,
    state_d: Path,
    emit: Callable[[str], None],
    run: Callable[..., Any] | None,
    which: Callable[[str], str | None] | None,
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """Admit SAFE workshop skills, hold REVIEW for human."""
    entered: list[str] = []
    review: list[dict[str, Any]] = []
    lines: list[str] = []
    for sk in skills:
        verdict = vet_skill(sk)
        if verdict.status == SAFE:
            if not dry_run:
                desc = skill_description(sk.path)
                res = adopt_skill(
                    sk, desc, home=home_dir, vault_data=vault, engine_root=engine,
                    state_dir=state_d, run=run, which=which,
                )
                note_key = "  + skill {name}: {note}" if res.adopted else "  ! skill {name}: {note}"
                line = t(note_key, name=sk.name, note=res.note)
                if res.adopted:
                    entered.append(f"skill:{sk.name}")
            else:
                entered.append(f"skill:{sk.name}")
                line = t("  + [dry-run] skill {name}", name=sk.name)
            emit(line)
            lines.append(line)
        elif verdict.status == REVIEW:
            review.append({"kind": "skill", "name": sk.name, "reasons": list(verdict.reasons)})
            line = t("  * skill {name} left for review ({reasons})", name=sk.name, reasons=verdict.joined())
            emit(line)
            lines.append(line)
    return entered, review, lines


def _reconcile_absent_servers(
    mcp_manifest_data: Mapping[str, Any] | None,
    live_server_names: set[str],
    consecutive_absent: dict[str, int],
    vault: Path,
    dry_run: bool,
    emit: Callable[[str], None],
) -> tuple[list[str], list[str]]:
    """Reconcile servers with origin: openclaw against live OpenClaw servers."""
    if not mcp_manifest_data:
        return [], []
    raw_servers = mcp_manifest_data.get("servers") or {}
    exited: list[str] = []
    lines: list[str] = []
    for s_name, s_spec in raw_servers.items():
        if not isinstance(s_spec, Mapping) or s_spec.get("origin") != "openclaw":
            continue
        key = f"mcp:{s_name}"
        openclaw_id = s_spec.get("openclaw_id") or s_name
        is_present = (s_name in live_server_names) or (openclaw_id in live_server_names)
        if is_present:
            consecutive_absent.pop(key, None)
        else:
            count = consecutive_absent.get(key, 0) + 1
            if count >= 2:
                if not dry_run:
                    retire_adopted_server(s_name, vault_data=vault)
                consecutive_absent.pop(key, None)
                exited.append(key)
                line = t("  - mcp {name}: absent for 2 consecutive passes; retired", name=s_name)
            else:
                consecutive_absent[key] = count
                line = t("  ? mcp {name}: absent from OpenClaw (pass 1/2; kept)", name=s_name)
            emit(line)
            lines.append(line)
    return exited, lines


def _reconcile_absent_skills(
    skills_manifest_data: Mapping[str, Any] | None,
    live_skill_names: set[str],
    consecutive_absent: dict[str, int],
    vault: Path,
    engine: Path,
    home_dir: Path,
    dry_run: bool,
    emit: Callable[[str], None],
) -> tuple[list[str], list[str]]:
    """Reconcile skills with OpenClaw provenance against live OpenClaw skills."""
    if not skills_manifest_data:
        return [], []
    raw_skills = skills_manifest_data.get("skills") or {}
    exited: list[str] = []
    lines: list[str] = []
    for sk_name, sk_spec in raw_skills.items():
        if not isinstance(sk_spec, Mapping):
            continue
        sk_dir = vault / "03-INFRA" / "agent-universal-layer" / "skills" / sk_name
        if not is_openclaw_skill(dict(sk_spec), sk_dir):
            continue
        key = f"skill:{sk_name}"
        openclaw_id = sk_spec.get("openclaw_id") or sk_name
        is_present = (sk_name in live_skill_names) or (openclaw_id in live_skill_names)
        if is_present:
            consecutive_absent.pop(key, None)
        else:
            count = consecutive_absent.get(key, 0) + 1
            if count >= 2:
                if not dry_run:
                    retire_adopted_skill(sk_name, vault_data=vault, engine_root=engine, home=home_dir)
                consecutive_absent.pop(key, None)
                exited.append(key)
                line = t("  - skill {name}: absent for 2 consecutive passes; retired", name=sk_name)
            else:
                consecutive_absent[key] = count
                line = t("  ? skill {name}: absent from OpenClaw (pass 1/2; kept)", name=sk_name)
            emit(line)
            lines.append(line)
    return exited, lines


def _admit_candidates(
    plan: Any,
    mcp: bool,
    skills: bool,
    dry_run: bool,
    home_dir: Path,
    vault: Path,
    engine: Path,
    state_d: Path,
    emit: Callable[[str], None],
    run: Callable[..., Any] | None,
    which: Callable[[str], str | None] | None,
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """Process newly discovered candidates for admission."""
    entered: list[str] = []
    pending_review: list[dict[str, Any]] = []
    lines: list[str] = []
    if mcp:
        ent_m, rev_m, l_m = _process_candidate_servers(plan.servers, dry_run, home_dir, vault, emit)
        entered.extend(ent_m)
        pending_review.extend(rev_m)
        lines.extend(l_m)
    if skills:
        ent_s, rev_s, l_s = _process_candidate_skills(
            plan.skills, dry_run, home_dir, vault, engine, state_d, emit, run, which,
        )
        entered.extend(ent_s)
        pending_review.extend(rev_s)
        lines.extend(l_s)
    return entered, pending_review, lines


def _reconcile_absent(
    known: Any,
    loaded_cfg: Mapping[str, Any],
    cfg_file: Path,
    home_dir: Path,
    mcp: bool,
    skills: bool,
    consecutive_absent: dict[str, int],
    vault: Path,
    engine: Path,
    dry_run: bool,
    emit: Callable[[str], None],
) -> tuple[list[str], list[str]]:
    """Reconcile all absent resources across MCP and skills."""
    from nexgen_core.openclaw.adopt_plugins import scan_plugins
    plugin_srvs, _ = scan_plugins(cfg_file.parent, loaded_cfg, set())
    live_srv_names = set(adopt_scan.live_servers(loaded_cfg).keys()) | {s.name for s in plugin_srvs}
    live_sk_names = {s.name for s in adopt_scan.workshop_skills(loaded_cfg, cfg_file.parent, home_dir, set())}

    exited: list[str] = []
    lines: list[str] = []
    if mcp:
        ex_m, l_m = _reconcile_absent_servers(known.mcp, live_srv_names, consecutive_absent, vault, dry_run, emit)
        exited.extend(ex_m)
        lines.extend(l_m)
    if skills:
        ex_s, l_s = _reconcile_absent_skills(
            known.skills, live_sk_names, consecutive_absent, vault, engine, home_dir, dry_run, emit,
        )
        exited.extend(ex_s)
        lines.extend(l_s)
    return exited, lines


def _save_run_state(
    state_d: Path,
    consecutive_absent: dict[str, int],
    entered: list[str],
    exited: list[str],
    pending_review: list[dict[str, Any]],
) -> None:
    """Save the state of the completed adoption pass."""
    save_adopt_state(state_d, {
        "consecutive_absent": consecutive_absent,
        "last_pass": {
            "timestamp": datetime.now(UTC).isoformat(),
            "entered": entered,
            "exited": exited,
            "pending_review": pending_review,
        },
    })


def run_auto_adopt(
    *,
    skills: bool = True,
    mcp: bool = True,
    dry_run: bool = False,
    home: Path | None = None,
    vault_data: Path | None = None,
    engine_root: Path | None = None,
    state_dir: Path | None = None,
    config_path: Path | None = None,
    out: Callable[[str], None] | None = None,
    gateway_url: str | None = None,
    gateway_token: str | None = None,
    run: Callable[..., Any] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> AutoAdoptResult:
    """Execute the automatic adoption and retirement pass."""
    emit = out or print
    home_dir, vault = resolve_home(home), resolve_vault_data(resolve_home(home), vault_data)
    engine, state_d = resolve_engine_root(home_dir, engine_root), resolve_state_dir(home_dir, override=state_dir)
    cfg_file = Path(config_path) if config_path is not None else openclaw_config(home_dir)

    loaded_cfg, cfg_err = _read_openclaw_safely(cfg_file, gateway_url, gateway_token)
    if loaded_cfg is None:
        note = t("OpenClaw configuration could not be read: {error}. Nothing adopted or removed.", error=cfg_err)
        emit(note)
        return AutoAdoptResult(ok=True, entered=[], exited=[], pending_review=[], notes=[note], lines=[note])

    known = adopt_scan.library(vault)
    if (known.mcp is None and mcp) or (known.skills is None and skills):
        note = t("Canonical library manifest could not be read; skipping auto pass.")
        emit(note)
        return AutoAdoptResult(ok=False, entered=[], exited=[], pending_review=[], notes=[note], lines=[note])

    plan = adopt_scan.scan(
        config_path=cfg_file, state_dir=cfg_file.parent, home=home_dir,
        skills_manifest=known.skills, mcp_manifest=known.mcp, config=loaded_cfg,
    )
    entered, pending_review, lines = _admit_candidates(
        plan, mcp, skills, dry_run, home_dir, vault, engine, state_d, emit, run, which,
    )

    state = load_adopt_state(state_d)
    consecutive_absent = dict(state.get("consecutive_absent") or {})
    exited, rec_lines = _reconcile_absent(
        known, loaded_cfg, cfg_file, home_dir, mcp, skills, consecutive_absent, vault, engine, dry_run, emit,
    )
    lines.extend(rec_lines)

    if not dry_run:
        _save_run_state(state_d, consecutive_absent, entered, exited, pending_review)

    if not lines:
        empty_msg = t("Everything is aligned: no new safe items to adopt, nothing absent to retire.")
        emit(empty_msg)
        lines.append(empty_msg)

    return AutoAdoptResult(ok=True, entered=entered, exited=exited, pending_review=pending_review, notes=list(plan.notes), lines=lines)

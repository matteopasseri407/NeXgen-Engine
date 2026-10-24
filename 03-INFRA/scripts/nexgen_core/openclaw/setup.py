"""`nexgen openclaw setup`: from nothing to a harness that answers.

One job: decide the order of the nine steps, stop at the first one that cannot
succeed, and hand the work of each step to `setup_steps`. The order is not
cosmetic. The package decides which binary exists; onboarding decides what the
configuration file already holds; the plan is computed from both; and the write
happens while the gateway is stopped, because OpenClaw does not reload a changed
`plugins` block hot. A second run with nothing to change writes nothing and
restarts nothing.

`restart` and `pair` live here too: they are the two operations a person needs
after a setup, they act on the same machine, and they print in the same voice.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.openclaw.detect import Detection, detect
from nexgen_core.openclaw.doctor import collect as doctor_collect
from nexgen_core.openclaw.doctor import render as doctor_render
from nexgen_core.openclaw.pin import pinned_version
from nexgen_core.openclaw.plan import Plan, build_plan, render_plan
from nexgen_core.openclaw.profile import GATEWAY_PORT, Choices
from nexgen_core.openclaw.setup_agents import snapshot_dirs
from nexgen_core.openclaw.setup_steps import (
    START_TIMEOUT_SECONDS,
    Emit,
    Run,
    Setup,
    Which,
    default_run,
    ensure_installed,
    ensure_onboarded,
    module_step,
    phone_step,
    planned_commands,
    plugin_step,
    service_step,
    workspaces_step,
    write_step,
)
from nexgen_core.openclaw_pairing import approve as pairing_approve
from nexgen_core.openclaw_pairing import pending as pairing_pending
from nexgen_core.openclaw_service import OpenClawService, RunResult
from nexgen_core.paths import openclaw_config, resolve_home
from nexgen_core.processes import command_line_text

__all__ = ["START_TIMEOUT_SECONDS", "run_pair", "run_restart", "run_setup", "run_stop"]


def _step(emit: Emit, number: int, title: str) -> None:
    """One numbered heading, so a person can see which step stopped."""
    emit("")
    emit(f"[{number}/9] {title}")


def ask_yes_no(question: str) -> bool:
    """A yes/no question with a safe default, and no crash without a terminal."""
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:  # nexgen: allow reason="no terminal means no answer, and a question nobody can answer is a no, never a crash mid-setup"
        return False
    return answer.strip().lower() in ("y", "yes", "s", "si", "sì")


def _confirm(setup: Setup, yes: bool, confirm: Callable[[str], bool] | None) -> bool:
    """Step 3's question, skipped by `--yes` and answerable by a fake in tests."""
    if yes:
        return True
    ask = confirm or ask_yes_no
    return ask(t("Write this configuration and start the gateway?"))


@dataclass(frozen=True)
class _Decision:
    """The plan to apply, or the exit code of a run that stops here."""

    plan: Plan | None
    code: int


def _prepare(
    *,
    home: Path | None,
    env: Mapping[str, str] | None,
    detected: Detection | None,
    which: Which | None,
    run: Run | None,
    service: OpenClawService | None,
    dry_run: bool,
    engine_root: Path | None,
    vault_data: Path | None,
    stamp: str | None,
    out: Emit,
    platform: str | None,
) -> tuple[Setup, Detection]:
    """The context every step shares, and the snapshot it acts on."""
    values = dict(os.environ) if env is None else {str(k): str(v) for k, v in env.items()}
    home_dir = resolve_home(home)
    config_path = openclaw_config(home_dir, values)
    setup = Setup(
        home=home_dir,
        env=values,
        config_path=config_path,
        engine_root=engine_root,
        vault_data=vault_data,
        pinned=pinned_version(engine_root),
        dry_run=dry_run,
        out=out,
        which=which or shutil.which,
        run=run or default_run,
        service=service or OpenClawService(
            home=home_dir, state_dir=config_path.parent, port=GATEWAY_PORT, platform=platform,
        ),
        stamp=stamp or datetime.now().strftime("%Y%m%d-%H%M%S"),
        platform=platform,
    )
    snapshot = detected if detected is not None else detect(
        home=home_dir, env=values, which=setup.which, engine_root=engine_root,
        vault_data=vault_data, platform=platform,
    )
    return setup, snapshot


def _install_phase(setup: Setup, snapshot: Detection) -> tuple[Detection, set[str]] | None:
    """Steps 1 and 2, plus the state-directory snapshot onboarding must precede."""
    _step(setup.out, 1, t("Installation"))
    installed = ensure_installed(setup, snapshot)
    if installed is None:
        return None
    _step(setup.out, 2, t("Initial configuration"))
    before = snapshot_dirs(setup.state_dir)
    if not ensure_onboarded(setup):
        return None
    return installed, before


def _plan_phase(
    setup: Setup,
    installed: Detection,
    *,
    yes: bool,
    lan: bool,
    sviluppo: bool,
    confirm: Callable[[str], bool] | None,
) -> _Decision:
    """Step 3: the plan, the confirmation, and the dry run that stops after it."""
    _step(setup.out, 3, t("Plan and confirmation"))
    plan = build_plan(
        detected=installed, choices=Choices(lan=lan, sviluppo=sviluppo),
        home=setup.home, env=setup.env, engine_root=setup.engine_root, vault_data=setup.vault_data,
    )
    setup.out(render_plan(plan))
    if setup.dry_run:
        setup.out("")
        setup.out(t("Commands this run would execute:"))
        for line in planned_commands(setup, installed, plan):
            setup.out(f"  {line}" if line.startswith("# ") else f"  $ {line}")
        setup.out(t("Dry run: nothing was written and nothing was executed."))
        return _Decision(None, 0)
    if not _confirm(setup, yes, confirm):
        setup.out(t("Nothing written: the plan above was not applied."))
        return _Decision(None, 0)
    return _Decision(plan, 0)


def _apply(setup: Setup, installed: Detection, plan, before: set[str]) -> int:
    """Steps 4 to 9: write, workspaces, service, module, phone, doctor."""
    _step(setup.out, 4, t("Safe write"))
    plugin_step(setup)
    outcome = write_step(setup, plan)
    if outcome == "failed":
        return 1
    _step(setup.out, 5, t("Agent workspaces"))
    workspaces_step(setup, plan, before)
    _step(setup.out, 6, t("Gateway service"))
    if not service_step(setup, restarted=outcome == "written"):
        return 1
    _step(setup.out, 7, t("Module"))
    module_step(setup)
    _step(setup.out, 8, t("Phone"))
    phone_step(setup)
    _step(setup.out, 9, t("Doctor"))
    outcomes = doctor_collect(
        detected=installed, home=setup.home, env=setup.env, engine_root=setup.engine_root,
        vault_data=setup.vault_data, platform=setup.platform,
    )
    setup.out(doctor_render(outcomes))
    setup.out("")
    setup.out(t("Setup complete."))
    return 0


def run_setup(
    *,
    yes: bool = False,
    lan: bool = False,
    dry_run: bool = False,
    sviluppo: bool = True,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    detected: Detection | None = None,
    which: Which | None = None,
    run: Run | None = None,
    service: OpenClawService | None = None,
    confirm: Callable[[str], bool] | None = None,
    out: Emit = print,
    engine_root: Path | None = None,
    vault_data: Path | None = None,
    stamp: str | None = None,
    platform: str | None = None,
) -> int:
    """The nine steps, and the exit code a shell can act on."""
    setup, snapshot = _prepare(
        home=home, env=env, detected=detected, which=which, run=run, service=service,
        dry_run=dry_run, engine_root=engine_root, vault_data=vault_data, stamp=stamp,
        out=out, platform=platform,
    )
    installed_phase = _install_phase(setup, snapshot)
    if installed_phase is None:
        return 1
    installed, before = installed_phase
    decision = _plan_phase(
        setup, installed, yes=yes, lan=lan, sviluppo=sviluppo, confirm=confirm,
    )
    if decision.plan is None:
        return decision.code
    return _apply(setup, installed, decision.plan, before)


def run_restart(
    *,
    service: OpenClawService | None = None,
    home: Path | None = None,
    timeout_s: float = START_TIMEOUT_SECONDS,
    out: Emit = print,
) -> int:
    """`nexgen openclaw restart`: stop, start, and say how long the port took."""
    gateway = service or OpenClawService(home=home)
    out(t("Restarting the gateway: a cold start takes about five minutes."))
    result = gateway.restart(timeout_s=timeout_s)
    for argv in result.get("commands") or []:
        out("$ " + command_line_text(argv))
    elapsed = float(result.get("elapsed_s") or 0.0)
    if result.get("ok"):
        out(t("The gateway answers on 127.0.0.1:{port} after {seconds} seconds.",
              port=gateway.port, seconds=int(elapsed)))
        return 0
    out(t("The gateway did not answer: {error}", error=result.get("error") or t("no answer from the port")))
    out(t("Waited {seconds} seconds.", seconds=int(elapsed)))
    return 1


def run_stop(
    *,
    service: OpenClawService | None = None,
    home: Path | None = None,
    out: Emit = print,
) -> int:
    """`nexgen openclaw stop`: the gateway down before a hand edit of its file."""
    gateway = service or OpenClawService(home=home)
    result = gateway.stop()
    for argv in result.get("commands") or []:
        out("$ " + command_line_text(argv))
    if result.get("ok"):
        out(t("The gateway is stopped."))
        return 0
    pids = ", ".join(str(pid) for pid in result.get("remaining_pids") or [])
    out(t("The gateway did not stop: these processes are still alive ({pids}).", pids=pids))
    return 1


def run_pair(
    *,
    approve_id: str | None = None,
    yes: bool = False,
    runner: Callable[[list[str]], RunResult] | None = None,
    confirm: Callable[[str], bool] | None = None,
    out: Emit = print,
) -> int:
    """`nexgen openclaw pair`: what is waiting, and the approval of one request."""
    ask = confirm or ask_yes_no
    listing = pairing_pending(runner=runner)
    devices = listing.get("devices") or []
    if devices:
        out(t("Requests waiting for approval:"))
        for device in devices:
            label = device.get("summary") or device.get("status") or ""
            out(f"  {device.get('id')}  {label}".rstrip())
    else:
        out(t("No device is waiting to be paired."))
    if not approve_id:
        if devices:
            out(t("Approve one with 'nexgen openclaw pair --approve <id>'."))
        return 0
    if not yes and not ask(t("Approve device {id}?", id=approve_id)):
        out(t("Nothing approved."))
        return 0
    outcome = pairing_approve(approve_id, runner=runner)
    if outcome.get("ok"):
        out(t("Device {id} approved.", id=approve_id))
        return 0
    out(t("The approval failed: {error}", error=outcome.get("error") or t("no reason given")))
    return 1

"""OpenClaw cron job registration and convergence for NeXgen Engine.

nexgen: one-job reason="declare, converge and switch off the OpenClaw automation jobs the engine owns"
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from nexgen_core.i18n import t
from nexgen_core.openclaw import automations
from nexgen_core.openclaw.setup_steps import openclaw_bin

if TYPE_CHECKING:
    from nexgen_core.openclaw.setup_steps import Setup


def groom_job_step(setup: Setup) -> None:
    """Registers the hourly groom as an OpenClaw command job, once.

    nexgen: one-job reason="declare and converge the hourly context-guard groom cron job in OpenClaw with no-deliver"
    The job keeps every agent chat small: idle chats are compacted, and a chat
    that grows past the limit is compacted even while in use. Without the job
    the policy in the config is only a promise.
    """
    if setup.dry_run:
        setup.out(t("  Context guard: skipped in dry-run."))
        return
    engine = setup.which("nexgen")
    if not engine:
        setup.out(t("  Context guard: 'nexgen' is not on PATH, job not registered."))
        return
    result = setup.run([
        openclaw_bin(setup), "cron", "add", "nexgen-context-guard",
        "--cron", "0 * * * *",
        "--command", f"{engine} openclaw groom",
        "--declaration-key", automations.CONTEXT_GUARD_DECLARATION_KEY,
        "--display-name", "Context guard",
        "--no-deliver",
    ])
    if result.returncode == 0:
        setup.out(t("  Context guard: hourly job registered."))
        return
    setup.out(t("  Context guard: the job was not registered, the gateway refused it."))


#: The cadence a check keeps when somebody asks for one: six hours. A periodic
#: check answers "is anything wrong", not "what happened this minute".
HEARTBEAT_EVERY = "6h"
#: What `squad.yaml` may ask for under `heartbeat.mode`: a cheap check (the default), a
#: full one, or none at all.
HEARTBEAT_MODES = ("light", "full", "off")


def heartbeat_block(setup: Setup) -> dict[str, object]:
    """The `heartbeat` block of `squad.yaml`, or an empty mapping when it says nothing.

    OpenClaw provisions a proactive check on explicit setup, and it is an ordinary
    automation job nobody writing to a bot asked for: a model turn every 30 minutes in
    the agent's main conversation. The engine therefore converges it, and this block is
    the person's hand on the switch:

        heartbeat:
          mode: full      # light (default) | full | off
          every: 6h
          model: ollama/llama3.2

    `mode: full` keeps the capability exactly as OpenClaw provisioned it, `mode: off`
    removes it, and both `every` and `model` are optional. `enabled: true` and
    `enabled: false` are the older spelling of `light` and `off`, and are still read.
    """
    from nexgen_core.openclaw.squad import find_squad_file, load_squad

    squad = load_squad(find_squad_file(setup.vault_data, home=setup.home))
    block = squad.get("heartbeat") if isinstance(squad, dict) else None
    return dict(block) if isinstance(block, dict) else {}


def _mode_value(value: object) -> str | None:
    """One `mode` value in any shape YAML may hand over, or None when it names nothing.

    YAML 1.1 reads `off`, `no` and `false` as booleans, so a block that says `mode: off`
    arrives here as `False`. Reading only strings would turn that into the default and
    silently keep a check somebody switched off.
    """
    if isinstance(value, bool):
        return "light" if value else "off"
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text in HEARTBEAT_MODES:
        return text
    if text in ("true", "yes", "on", "1"):
        return "light"
    if text in ("false", "no", "0"):
        return "off"
    return None


def requested_mode(block: dict[str, object]) -> str:
    """Which of the three the person asked for: `light` when they said nothing.

    Light is the default because it keeps every capability of the check -- it still
    fires, still answers "is anything wrong", still delivers -- and removes the two
    things that make it expensive: the agent's own conversation and its tool surface.
    An unrecognised value is read as the default rather than as a fourth mode: a typo
    must not silently switch a job off.
    """
    return _mode_value(block.get("mode")) or _mode_value(block.get("enabled")) or "light"


def _light_edit(binary: str, job: dict[str, object], block: dict[str, object], model: str | None) -> list[str]:
    """The edit that turns a provisioned check into a cheap one.

    Both flags are the documented ones: `--light-context` skips the workspace bootstrap
    files and applies to isolated jobs only, which is why the session moves with it, and
    `--tools ""` disables every agent tool for the run. Without the session move the
    check runs in the agent's main conversation and re-sends whatever that conversation
    has grown to.
    """
    every = block.get("every")
    argv = [
        binary, "automations", "edit", automations.job_id(job),
        "--every", every.strip() if isinstance(every, str) and every.strip() else HEARTBEAT_EVERY,
        "--session", "isolated",
        "--light-context",
        "--tools", "",
    ]
    if model:
        argv += ["--model", model]
    return argv


def light_model(setup: Setup, block: dict[str, object]) -> str | None:
    """The model this check runs on: the block's own, or the utility model in force.

    Utility work is the closest thing to a check in price and shape, and `setup` has
    just written it where the file keeps it, so one key answers both questions. The
    proposal is not read here on purpose: the file in force is what the job will resolve
    at run time, including a value the person wrote by hand.
    """
    from nexgen_core.openclaw.config_file import read_config

    declared = block.get("model")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    config, _ = read_config(setup.config_path)
    agents = config.get("agents") if isinstance(config, dict) else None
    defaults = agents.get("defaults") if isinstance(agents, dict) else None
    value = defaults.get("utilityModel") if isinstance(defaults, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        primary = value.get("primary")
        if isinstance(primary, str) and primary.strip():
            return primary.strip()
    return None


def heartbeat_job_step(setup: Setup) -> None:
    """Converges the proactive-check job OpenClaw provisions: light, full, or switched off.

    A heartbeat is an automation job in this release, not the `agents.defaults.heartbeat`
    key the engine used to write, and the job OpenClaw creates runs every 30 minutes in
    the agent's main conversation: 48 model turns a day over whatever that conversation
    has grown to, for a bot that may be answering nobody. The setup therefore converges
    the job itself, and the default is the cheap one: a fresh isolated context, no
    bootstrap files, no tools, every six hours, on the utility model. The capability is
    kept; what goes is the price.

    `heartbeat.mode: full` in `squad.yaml` leaves the check as OpenClaw provisioned it
    (or as the block's own `every`/`model` asks), and `mode: off` switches it off. The
    gateway is up by now (step 6 started it), so the scheduler can be asked. A scheduler
    that does not answer is said out loud and the run continues: a setup that stopped
    here would be a setup a person cannot finish on a slow machine.
    """
    if setup.dry_run:
        setup.out(t("  Proactive check: skipped in dry-run."))
        return
    binary = openclaw_bin(setup)
    listed = setup.run([binary, "automations", "list", "--all", "--json"], setup.env)
    jobs = automations.jobs_from_stdout(listed.stdout) if listed.returncode == 0 else None
    if jobs is None:
        setup.out(t("  Proactive check: the scheduler did not answer, so no job was changed. "
                    "Check it with 'openclaw automations list --all'."))
        return
    matches = automations.heartbeat_jobs(jobs)
    block = heartbeat_block(setup)
    mode = requested_mode(block)
    if mode == "off":
        running = [job for job in matches if automations.is_enabled(job)]
        if not running:
            setup.out(t("  Proactive check: no enabled job runs it."))
            return
        for job in running:
            _switch_off(setup, binary, job)
        return
    if not matches:
        setup.out(t("  Proactive check: no automation job matches it, so there was nothing to converge."))
        return
    if mode == "full":
        _converge_full(setup, binary, matches, block)
        return
    _converge_light(setup, binary, matches, block, light_model(setup, block))


def _converge_light(
    setup: Setup, binary: str, jobs: list[dict[str, object]], block: dict[str, object],
    model: str | None,
) -> None:
    """Leave the check running light: its own context, no bootstrap files, no tools."""
    every = block.get("every") if isinstance(block.get("every"), str) else HEARTBEAT_EVERY
    for job in jobs:
        name = automations.job_name(job)
        result = setup.run(_light_edit(binary, job, block, model), setup.env)
        if result.returncode != 0:
            setup.out(t("  Proactive check: '{name}' could not be made light.", name=name))
            continue
        if not automations.is_enabled(job):
            enabled = setup.run([binary, "automations", "enable", automations.job_id(job)], setup.env)
            if enabled.returncode != 0:
                setup.out(t("  Proactive check: '{name}' was made light but could not be switched on.", name=name))
                continue
        setup.out(t("  Proactive check: '{name}' runs every {every} in its own context, without tools.",
                    name=name, every=every))


def _converge_full(
    setup: Setup, binary: str, jobs: list[dict[str, object]], block: dict[str, object],
) -> None:
    """Leave the check as the person asked for it: the agent's conversation and its tools."""
    every = block.get("every") if isinstance(block.get("every"), str) and block.get("every") else None
    model = block.get("model") if isinstance(block.get("model"), str) and block.get("model") else None
    for job in jobs:
        name = automations.job_name(job)
        job_id = automations.job_id(job)
        if every or model:
            edit = [binary, "automations", "edit", job_id]
            if every:
                edit += ["--every", every]
            if model:
                edit += ["--model", model]
            result = setup.run(edit, setup.env)
            if result.returncode != 0:
                setup.out(t("  Proactive check: '{name}' could not be edited.", name=name))
                continue
        if not automations.is_enabled(job):
            enabled = setup.run([binary, "automations", "enable", job_id], setup.env)
            if enabled.returncode != 0:
                setup.out(t("  Proactive check: '{name}' could not be switched on.", name=name))
                continue
        setup.out(t("  Proactive check: '{name}' runs as OpenClaw provisioned it, every {every}.",
                    name=name, every=every or automations.schedule_text(job)))


def _switch_off(setup: Setup, binary: str, job: dict[str, object]) -> None:
    """Switch one provisioned check off, and say what it used to cost."""
    name = automations.job_name(job)
    result = setup.run([binary, "automations", "disable", automations.job_id(job)], setup.env)
    if result.returncode != 0:
        setup.out(t("  Proactive check: '{name}' could not be switched off.", name=name))
        return
    setup.out(t("  Proactive check: '{name}' is off (it ran every {every}).",
                name=name, every=automations.schedule_text(job)))

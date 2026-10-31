"""One job: bring the OpenClaw installed on this machine onto the release the engine pins.

Why it exists: the pin was only ever applied by `nexgen openclaw setup`, run by
hand, so the fleet drifted. A VPS gateway ran the pinned release while a desktop
node stayed weeks behind, and nothing in the recurring guard noticed. OpenClaw
ships a stable release every few days, so following the pin has to be part of the
cycle `agent-sync.service` already runs on every machine.

The rules that make it safe:

- a home with no OpenClaw configuration is left alone: the package can be on PATH
  without ever having run here, and there would be no gateway or node to restart;
- an install only happens when the Node that owns the running OpenClaw can run the
  pinned release; the engine never installs Node;
- an install only happens when this machine is provably free: an idle gateway inside the
  night window, an idle node service at any hour (`align_window.py` owns that rule);
- the npm used is the one beside that Node, so an nvm machine updates its own
  prefix instead of another release's;
- an upgrade that cannot be restarted and verified is rolled back to the release
  that was installed, and the pin is not retried for 24 hours.

It reports an outcome and never raises: a guard phase may fail, but it may not
take the cycle down with it. Every process call is injected (`run`, `service`,
`smoke`, `list_sessions`) so a test drives the whole decision with fakes.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from nexgen_core import files
from nexgen_core.i18n import t
from nexgen_core.openclaw import align_node, align_window, setup_commands
from nexgen_core.openclaw.detect import Installation, _detect_openclaw
from nexgen_core.openclaw.host_preflight import npm_prefix, read_node_version
from nexgen_core.openclaw.node_only import detect_node_only
from nexgen_core.openclaw.node_transition import detect_local_gateway
from nexgen_core.openclaw.pin import node_satisfies, pinned_version, required_node_version
from nexgen_core.openclaw_service import OpenClawService, RunResult
from nexgen_core.paths import openclaw_config, resolve_home, resolve_state_dir

#: What one alignment ended as. `FAILED` is the only one the guard turns into a failed phase.
UNCHANGED = "unchanged"
ALIGNED = "aligned"
SKIPPED = "skipped"
WARN = "warn"
FAILED = "failed"

#: The pin that failed here, and when: no retry until this much time has passed.
STATE_FILE = "openclaw-version-align.json"
RETRY_SECONDS = 24 * 60 * 60

#: What this machine runs, which decides what has to be restarted after an install.
GATEWAY = "gateway"
NODE = "node"
NOTHING = "nothing"

#: `(argv, env)` for a program; the same shape `host_preflight` and the setup steps use.
Run = Callable[..., RunResult]
Which = Callable[[str], "str | None"]
Sessions = Callable[[str], "list[dict] | None"]


@dataclass(frozen=True)
class Outcome:
    """What one cycle did, in the words a person reads, and the one action left."""

    state: str
    line: str = ""
    action: str = ""


@dataclass(frozen=True)
class _Release:
    """The one install this cycle performs, and everything its rollback would need."""

    pinned: str
    previous: str
    npm: str
    role: str
    binary: str | None
    service: OpenClawService | None
    smoke: Callable[[], int] | None
    memory: Path
    moment: float


def align(
    home: Path | None = None,
    engine_root: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    which: Which | None = None,
    run: Run | None = None,
    service: OpenClawService | None = None,
    smoke: Callable[[], int] | None = None,
    list_sessions: Sessions | None = None,
    node_children: align_window.Children | None = None,
    clock: Callable[[], float] | None = None,
    state_dir: Path | None = None,
    gateway_present: Callable[[], bool] | None = None,
) -> Outcome:
    """The whole decision: compare, check the floor, install, restart, verify, or roll back."""
    try:
        return _align(
            home=home, engine_root=engine_root, env=env, platform=platform, which=which,
            run=run, service=service, smoke=smoke, list_sessions=list_sessions,
            node_children=node_children, clock=clock, state_dir=state_dir,
            gateway_present=gateway_present,
        )
    except Exception as exc:  # nexgen: allow reason="the guard records this phase's outcome; an unexpected error must not take the cycle down"
        return Outcome(FAILED, t("Aligning OpenClaw with the pin failed: {error}", error=exc))


def _align(
    *, home: Path | None, engine_root: Path | None, env: Mapping[str, str] | None,
    platform: str | None, which: Which | None, run: Run | None, service: OpenClawService | None,
    smoke: Callable[[], int] | None, list_sessions: Sessions | None,
    node_children: align_window.Children | None, clock: Callable[[], float] | None,
    state_dir: Path | None, gateway_present: Callable[[], bool] | None,
) -> Outcome:
    """`align`, with the defaults resolved once: no branch below reads the machine twice."""
    from nexgen_core.scheduler import host_mutations_disabled

    if host_mutations_disabled():
        return Outcome(UNCHANGED)
    values = dict(os.environ) if env is None else {str(k): str(v) for k, v in env.items()}
    home_dir = resolve_home(home)
    system = platform or sys.platform
    lookup = which or shutil.which
    runner = run or _default_run
    moment = (clock or _now)()
    pinned = pinned_version(engine_root)
    values = _owner_node_first(values, lookup)
    node, npm = _owner_tools(lookup, runner, values, system)
    config = openclaw_config(home_dir, values)
    if not config.is_file():
        return Outcome(UNCHANGED, t("OpenClaw has no configuration here: nothing runs on this machine."))
    install = _installed(home_dir, values, config, runner, _owner_which(lookup, node, npm))

    if not install.installed:
        return Outcome(UNCHANGED, t("OpenClaw is not installed on this machine: nothing to align here."))
    if pinned is None:
        return Outcome(
            WARN, t("The engine cannot read its OpenClaw pin, so it cannot align what is installed."),
            t("Check agent-universal-layer/openclaw/pin.json: it holds the release the engine expects."),
        )
    if install.version == pinned:
        _forget_failure(_state_folder(state_dir, home_dir))
        return Outcome(UNCHANGED)
    memory = _state_folder(state_dir, home_dir)
    blocked = _not_yet(engine_root, install, pinned, node, npm, runner, values, memory, moment)
    if blocked is not None:
        return blocked
    role = _role(home_dir, values, system, gateway_present)
    binary = lookup("openclaw")
    postponed = align_window.postponement(
        runs_gateway=role == GATEWAY, runs_node=role == NODE, moment=moment, binary=binary,
        env=values, run=run, list_sessions=list_sessions, platform=system,
        children=node_children,
    )
    if postponed is not None:
        return Outcome(SKIPPED, postponed)
    service, smoke = _gateway_tools(role, service, smoke, home_dir, system, runner, values, binary)
    release = _Release(
        pinned=pinned, previous=install.version or "?", npm=npm, role=role, binary=binary,
        service=service, smoke=smoke, memory=memory, moment=moment,
    )
    return _upgrade(release, runner, values)


def _not_yet(
    engine_root: Path | None, install: Installation, pinned: str, node: str | None, npm: str | None,
    run: Run, env: dict[str, str], memory: Path, moment: float,
) -> Outcome | None:
    """What stops this cycle from installing the pin, before anything on the machine is touched.

    The Node floor comes first: installing a release this Node cannot run would break the
    gateway it is serving. Then npm, which lives beside that Node and nowhere else. Then the
    pin that failed here in the last day, which the next cycle must not retry.
    """
    floor = _floor(engine_root, install, pinned, node, run, env)
    if floor is not None:
        return floor
    if npm is None:
        return Outcome(
            WARN, t("OpenClaw {version} is installed but npm is not beside its Node: it cannot be updated from here.",
                    version=install.version or "?"),
            t("Install Node from {url}: it brings npm with it.", url=setup_commands.NODE_DOWNLOAD_URL),
        )
    if _recent_failure(memory, pinned, moment):
        return Outcome(SKIPPED, t(
            "The last attempt to install OpenClaw {pinned} failed less than 24 hours ago: the next attempt is "
            "after that, or as soon as the pin changes.", pinned=pinned))
    return None


def _gateway_tools(
    role: str, service: OpenClawService | None, smoke: Callable[[], int] | None, home: Path,
    system: str, run: Run, env: dict[str, str], binary: str | None,
) -> tuple[OpenClawService | None, Callable[[], int] | None]:
    """The two collaborators only a gateway needs: its controller and its smoke test."""
    if role != GATEWAY:
        return service, smoke
    return (
        service or OpenClawService(home=home, platform=system),
        smoke or _gateway_smoke(run, env, binary),
    )


def _upgrade(release: _Release, run: Run, env: dict[str, str]) -> Outcome:
    """Install, restart, verify; or put the previous release back and remember the failed pin."""
    installed = run(setup_commands.install_argv(release.npm, release.pinned), env)
    if installed.returncode != 0:
        _remember_failure(release.memory, release.pinned, release.moment)
        return Outcome(FAILED, t("Installing OpenClaw {pinned} failed (exit {code}): {detail}",
                                 pinned=release.pinned, code=installed.returncode, detail=_tail(installed)))
    problem = (_restart(release.role, release.service, release.binary, run, env)
               or _verify(release.role, release.smoke, release.binary, run, env))
    if problem is None:
        _forget_failure(release.memory)
        return Outcome(ALIGNED, _success_line(release.role, release.pinned))
    rollback = _rollback(release.role, release.service, release.binary, release.npm, release.previous, run, env)
    _remember_failure(release.memory, release.pinned, release.moment)
    return Outcome(FAILED, t("OpenClaw {pinned} did not work here ({problem}): {previous} was put back.{rollback}",
                             pinned=release.pinned, problem=problem, previous=release.previous,
                             rollback=rollback))


def _default_run(argv: list[str], env: Mapping[str, str] | None = None) -> RunResult:
    """Run a program with both streams captured and no console window; a failure is an answer."""
    from nexgen_core.processes import no_window_kwargs

    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            env=dict(env) if env is not None else None, **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:  # nexgen: allow reason="a program that cannot start is an answer to this step, and its sentence is the report"
        return RunResult(1, "", str(exc))
    return RunResult(completed.returncode, completed.stdout, completed.stderr)


def _now() -> float:
    """The wall clock, in one place so a test moves time instead of waiting."""
    import time

    return time.time()


def _owner_tools(which: Which, run: Run, env: Mapping[str, str],
                 system: str) -> tuple[str | None, str | None]:
    """The Node and the npm that own the OpenClaw this machine runs.

    An nvm machine keeps one bin folder per Node release, and each `npm` installs into
    its own prefix. Taking the npm from PATH can therefore update a release the running
    gateway never reads: the folder OpenClaw itself was installed into decides. A user
    prefix (`~/.npm-global`, `~/.local`) holds OpenClaw without an npm beside it: there
    the npm on PATH owns it when its global prefix is that folder, and only then.
    """
    node = which("node")
    binary = which("openclaw")
    folder = Path(binary).parent if binary else (Path(node).parent if node else None)
    if folder is None:
        return node, which("npm")
    owner = _sibling(folder, "npm") or _prefix_owner(folder, which, run, env, system)
    return _sibling(folder, "node") or node, owner


def _owner_node_first(env: dict[str, str], which: Which) -> dict[str, str]:
    """The environment with the owning Node's folder first on PATH.

    npm and the `openclaw` CLI start with `#!/usr/bin/env node`: run under another Node
    found earlier on PATH (a stale `~/.local/bin/node` link on a desktop), npm reads that
    Node's global prefix, sees the old release there and installs into the wrong folder.
    """
    binary = which("openclaw")
    node = (_sibling(Path(binary).parent, "node") if binary else None) or which("node")
    if not node:
        return env
    folder = str(Path(node).parent)
    rest = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry and entry != folder]
    return {**env, "PATH": os.pathsep.join([folder, *rest])}


def _prefix_owner(folder: Path, which: Which, run: Run, env: Mapping[str, str], system: str) -> str | None:
    """The npm on PATH, when the global packages it installs land in `folder`."""
    npm = which("npm")
    prefix = npm_prefix(run, npm, env) if npm else None
    if prefix is None:
        return None
    installs_into = prefix if system == "win32" else prefix / "bin"
    return npm if _same_folder(installs_into, folder) else None


def _same_folder(left: Path, right: Path) -> bool:
    """Two spellings of one folder, links followed where the filesystem can answer."""
    try:
        return left.resolve() == right.resolve()
    except OSError:  # nexgen: allow reason="an unresolvable path compares as written"
        return left == right


def _sibling(folder: Path, name: str) -> str | None:
    """The executable `name` in `folder`, in the suffixes npm uses on this platform."""
    for suffix in ("", ".exe", ".cmd"):
        candidate = folder / f"{name}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return None


def _owner_which(which: Which, node: str | None, npm: str | None) -> Which:
    """`which`, with Node and npm answered from the folder that owns this OpenClaw."""
    chosen = {"node": node, "npm": npm}

    def lookup(name: str) -> str | None:
        if name in chosen:
            return chosen[name]
        return which(name)

    return lookup


def _installed(home: Path, env: dict[str, str], config: Path, run: Run, which: Which) -> Installation:
    """The OpenClaw package this machine has, read by the same code `setup` and the doctor use."""
    return _detect_openclaw(home, env, _detect_runner(run), which, config)


def _detect_runner(run: Run) -> Callable[..., RunResult]:
    """`detect` asks with subprocess keywords; every step here asks with `(argv, env)`."""
    def call(argv: list[str], env: Mapping[str, str] | None = None, **_ignored: object) -> RunResult:
        return run(argv, env)

    return call


def _floor(engine_root: Path | None, install: Installation, pinned: str, node: str | None, run: Run,
           env: dict[str, str]) -> Outcome | None:
    """The Node the pin requires, or the warning that says the engine will not install it."""
    requirement = required_node_version(engine_root) or install.node_requirement
    version = read_node_version(run, node or "node", env)
    if not requirement or node_satisfies(version, requirement):
        return None
    return Outcome(
        WARN,
        t("OpenClaw {version} is installed and the engine pins {pinned}, but Node {node} does not satisfy "
          "{requirement}.", version=install.version or "?", pinned=pinned,
          node=version or t("(unknown)"), requirement=requirement),
        t("Install a Node release in the range {requirement}: the engine never installs Node.",
          requirement=requirement),
    )


def _role(home: Path, env: dict[str, str], system: str, gateway_present: Callable[[], bool] | None) -> str:
    """What this machine runs: the node service, the gateway, or only the package."""
    if detect_node_only(home, platform=system, env=env):
        return NODE
    present = gateway_present or (lambda: _local_gateway(home, system))
    return GATEWAY if present() else NOTHING


def _local_gateway(home: Path, system: str) -> bool:
    """Whether a gateway service is installed here, from its files and never by starting one."""
    gateway = detect_local_gateway(home=home, platform=system, runner=_inert)
    return gateway.present


def _inert(_argv: list[str], _timeout: float) -> RunResult:
    """A runner that starts nothing: deciding a role must not launch a CLI."""
    return RunResult(127, "", "")


def _gateway_smoke(run: Run, env: dict[str, str], binary: str | None) -> Callable[[], int]:
    """The existing smoke test, with this phase's injected runner and the owner's CLI."""
    from nexgen_core.openclaw.smoke import run_smoke

    def runner(argv: list[str], _timeout: float) -> RunResult:
        return run([binary, *argv[1:]] if binary else argv, env)

    def fire() -> int:
        return run_smoke(run=runner, out=lambda _line: None)

    return fire


def _restart(role: str, service: OpenClawService | None, binary: str | None, run: Run,
             env: dict[str, str]) -> str | None:
    """Restart what this machine runs; the sentence that says what went wrong, or None."""
    if role == GATEWAY:
        if service is None:
            return t("the gateway service controller is not available")
        result = service.restart()
        return None if result.get("ok") else str(result.get("error") or t("the gateway did not restart"))
    if role == NODE:
        return align_node.restart(run, binary or "openclaw", env)
    return None


def _verify(role: str, smoke: Callable[[], int] | None, binary: str | None, run: Run,
            env: dict[str, str]) -> str | None:
    """Prove the restarted process answers; the sentence that says it does not, or None."""
    if role == GATEWAY:
        if smoke is None:
            return t("no smoke test is available to prove the gateway works")
        return None if smoke() == 0 else t("the smoke test failed")
    if role == NODE:
        return align_node.running(run, binary or "openclaw", env)
    return None


def _rollback(role: str, service: OpenClawService | None, binary: str | None, npm: str,
              previous: str, run: Run, env: dict[str, str]) -> str:
    """Put the release that was there back, and say so when even that fails."""
    if not previous:
        return ""
    reinstalled = run(setup_commands.install_argv(npm, previous), env)
    if reinstalled.returncode != 0:
        return t(" Reinstalling {previous} failed too ({detail}).", previous=previous, detail=_tail(reinstalled))
    restarted = _restart(role, service, binary, run, env)
    return "" if restarted is None else t(" Restarting it failed too ({error}).", error=restarted)


def _success_line(role: str, pinned: str) -> str:
    """One sentence that says what was installed and what answered afterwards."""
    if role == NODE:
        return t("OpenClaw {pinned} is installed and the node service answers.", pinned=pinned)
    if role == GATEWAY:
        return t("OpenClaw {pinned} is installed and the gateway answers.", pinned=pinned)
    return t("OpenClaw {pinned} is installed on this machine.", pinned=pinned)


_tail = align_node.tail


def _state_folder(state_dir: Path | None, home: Path) -> Path:
    """Where the failed pin is remembered: engine-owned state, beside the other markers."""
    return Path(state_dir) if state_dir is not None else resolve_state_dir(home)


def _read_failure(path: Path) -> dict:
    """What the last failed attempt recorded, or an empty record when there is none."""
    try:
        loaded = json.loads((path / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="a missing or unreadable marker means no pin is being held back"
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _recent_failure(directory: Path, pinned: str, moment: float) -> bool:
    """Whether this same pin failed here within the last 24 hours."""
    record = _read_failure(directory)
    if record.get("pin") != pinned:
        return False
    try:
        failed_at = float(record.get("failed_at") or 0)
    except (TypeError, ValueError):  # nexgen: allow reason="a malformed timestamp is not a reason to hold a pin back forever"
        return False
    return moment - failed_at < RETRY_SECONDS


def _remember_failure(directory: Path, pinned: str, moment: float) -> None:
    """Record the pin that failed, so the next cycle does not retry it all day."""
    files.atomic_write_text(
        directory / STATE_FILE,
        json.dumps({"pin": pinned, "failed_at": moment}, indent=2, sort_keys=True) + "\n",
    )


def _forget_failure(directory: Path) -> None:
    """A pin that arrived is not a failed pin: drop the marker."""
    try:
        (directory / STATE_FILE).unlink(missing_ok=True)
    except OSError:  # nexgen: allow reason="a marker that cannot be removed only repeats the safety rule, it does not break the install"
        return

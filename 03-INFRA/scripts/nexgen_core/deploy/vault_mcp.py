"""Rebuild the vault-mcp container from the engine this machine just installed.

nexgen: one-job reason="put one deployed container on the engine commit of the checkout that describes it, and put the previous image and source back when the rebuilt one does not answer"
One job: rebuild `vault-mcp` from this engine's source, prove it answers the MCP
protocol, and undo both halves when it does not.

Why this has to exist at all: on a Cloud-Server install the VPS updates its own
engine on a timer, while the containers under `03-INFRA/deploy/` keep running
the code they were built from months earlier. Nobody compared the two. The
image now carries the commit it was built from and the deploy is the one place
that writes it, so the answer to "is the server on the same code as the engine"
is a comparison instead of an archaeology of hand commands.

The backup is deliberately two things: the source directory as it was (a file
copy, because the copy on a server may not be a git checkout) and the running
image under its own tag (because a rebuild can fail at any layer).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexgen_core.deploy import alignment, probe
from nexgen_core.errors import NexgenError
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_state_dir
from nexgen_core.processes import no_window_kwargs
from nexgen_core.stack import secrets
from nexgen_core.stack.services import by_name

#: The service this module deploys, and the stack folder that describes it.
SERVICE = "vault-mcp"
STACK_DIR = Path("deploy") / "vault-mcp"
COMPOSE_FILE = "docker-compose.yml"

#: The variable a rebuild reads its source directory from, when the container is
#: built from a copy rather than from the engine tree itself.
SOURCE_ENV = "NEXGEN_VAULT_MCP_SRC"

#: What a rebuild writes into the image, and what the health route publishes.
COMMIT_ENV = "NEXGEN_ENGINE_COMMIT"

#: The suffix of the tag the previous image is kept under: `vault-mcp:0.5.0`
#: becomes `vault-mcp:0.5.0-pre-deploy`, which composes fine with a registry
#: prefix because the tag is the last colon-separated segment.
ROLLBACK_SUFFIX = "-pre-deploy"

#: A rebuilt container needs a moment to import its server and answer.
PROBE_ATTEMPTS = 30
PROBE_DELAY_SECONDS = 2.0

COMPOSE_TIMEOUT_SECONDS = 900

#: The files that make up the service, copied into a build context elsewhere.
SOURCE_ENTRIES = ("src", "pyproject.toml", "Dockerfile", "README.md", "LICENSE")


class DeployError(NexgenError, RuntimeError):
    """The rebuild could not be done, with what was restored and what to do next."""


@dataclass(frozen=True)
class DeployResult:
    """What a rebuild did, in the order it did it."""

    commit: str
    source: Path
    backup: Path
    previous_image: str | None
    tools: tuple[str, ...]


def run_command(args: Sequence[str], *, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """One command, with the mount's environment plus what this step adds."""
    merged = {**os.environ, **(env or {})}
    try:
        return subprocess.run(
            list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=COMPOSE_TIMEOUT_SECONDS, env=merged, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeployError(t("'{command}' could not run ({error})", command=args[0], error=exc)) from exc


def _last_line(result: subprocess.CompletedProcess[str]) -> str:
    """The one line of a failed command worth showing, never the whole output."""
    lines = [line.strip() for line in f"{result.stderr}\n{result.stdout}".splitlines() if line.strip()]
    return lines[-1] if lines else f"exit {result.returncode}"


def engine_tree(engine_root: Path) -> Path:
    """The stack folder inside an installed engine: `03-INFRA/deploy/vault-mcp`."""
    return Path(engine_root) / STACK_DIR


def env_path(engine_root: Path) -> Path:
    """The secrets file shared by the stacks. Read for one token, never printed."""
    return Path(engine_root) / "deploy" / ".env"


def source_dir(engine_root: Path, override: Path | None = None, *, environ: Mapping[str, str] | None = None) -> Path:
    """Where the running container is built from.

    The engine's own tree unless something says otherwise, because on a
    correctly installed server that is the copy that gets updated. A container
    built from a plain copy of the source elsewhere names it once in
    `NEXGEN_VAULT_MCP_SRC`, in the process or in the stack's `.env`; the copy is
    then refreshed from the engine tree on every deploy.
    """
    if override is not None:
        return Path(override).expanduser().resolve()
    values = os.environ if environ is None else environ
    named = values.get(SOURCE_ENV) or secrets.read_env_file(env_path(engine_root)).get(SOURCE_ENV)
    if named:
        return Path(named).expanduser().resolve()
    return engine_tree(engine_root)


def _git_commit(engine_root: Path, runner: Callable[..., subprocess.CompletedProcess[str]]) -> str:
    """The full commit of the checkout being deployed, and a refusal outside one."""
    result = runner(["git", "-C", str(engine_root), "rev-parse", "HEAD"])
    commit = (result.stdout or "").strip()
    if result.returncode != 0 or not commit:
        raise DeployError(t(
            "the engine at {root} is not a git checkout, so nothing says which commit the image is built from",
            root=engine_root,
        ))
    return commit


def _current_image(runner: Callable[..., subprocess.CompletedProcess[str]]) -> str | None:
    """The image the running container was created from, or None when it is not running."""
    result = runner(["docker", "inspect", "--format", "{{.Config.Image}}", SERVICE])
    image = (result.stdout or "").strip()
    return image if result.returncode == 0 and image else None


def _sync_source(from_tree: Path, to_dir: Path, out: Callable[[str], None]) -> None:
    """Copy this engine's source into a build context that lives elsewhere.

    Nothing happens when the two are the same directory, which is what a server
    installed by the book has: the build context is the engine's own tree.
    """
    if from_tree.resolve() == to_dir.resolve():
        return
    try:
        for entry in SOURCE_ENTRIES:
            origin = from_tree / entry
            target = to_dir / entry
            if origin.is_dir():
                shutil.copytree(origin, target, dirs_exist_ok=True)
            elif origin.is_file():
                shutil.copy2(origin, target)
            else:
                raise DeployError(t("{origin} is missing from the engine tree", origin=origin))
    except OSError as exc:
        raise DeployError(t("the source could not be copied into {dir} ({error})", dir=to_dir, error=exc)) from exc
    out(t("  source refreshed from the engine tree in {dir}", dir=to_dir))


def _backup_source(source: Path, state_dir: Path) -> Path:
    """Keep the source as it was, in the machine's own state directory."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = state_dir / "deploy-backups" / f"{SERVICE}-{stamp}"
    try:
        shutil.copytree(source, target, dirs_exist_ok=True)
    except OSError as exc:
        raise DeployError(t("the previous source could not be saved ({error}); nothing was rebuilt", error=exc)) from exc
    return target


def _compose(compose: Path, env_file: Path, *args: str) -> list[str]:
    return ["docker", "compose", "--env-file", str(env_file), "-f", str(compose), *args]


def _roll_back(
    *,
    previous_image: str | None,
    backup: Path,
    source: Path,
    compose: Path,
    env_file: Path,
    runner: Callable[..., Any],
    out: Callable[[str], None],
) -> list[str]:
    """Put back the image and the source a failed rebuild replaced.

    Both halves are attempted and every outcome is recorded: a rollback that
    worked for the image but not for the source still has to say so, because the
    next person to look at the directory needs to know which one it is.
    """
    notes: list[str] = []
    if previous_image is None:
        notes.append(t("there was no previous image to go back to: the service is running the new one"))
    else:
        tag = f"{previous_image}{ROLLBACK_SUFFIX}"
        tagged = runner(["docker", "tag", tag, previous_image])
        restored = runner(_compose(compose, env_file, "up", "-d", "--no-build", SERVICE))
        if tagged.returncode == 0 and restored.returncode == 0:
            notes.append(t("the previous image is back: {image}", image=previous_image))
        else:
            notes.append(t("the previous image could not be restored ({detail})", detail=_last_line(restored)))
    try:
        shutil.copytree(backup, source, dirs_exist_ok=True)
    except OSError as exc:  # nexgen: allow reason="the image rollback is already the answer; a source copy that fails is reported, not raised over it"
        notes.append(t("the saved source could not be put back ({error})", error=exc))
    else:
        notes.append(t("the saved source is back in {dir}", dir=source))
    for note in notes:
        out(f"  {note}")
    return notes


def published_url() -> str:
    """The address the rebuilt container answers on, on this machine.

    Not the manifest's `VAULT_LIBRARY_URL`: that is the address the CLIs on a
    workstation use, and on a server it can name a tunnel that only exists
    there. The deploy probes the port the compose file publishes on this host.
    """
    service = by_name("vault-library")
    return f"http://127.0.0.1:{service.port if service else 8081}/mcp"


def _prepare(
    engine_root: Path, source: Path | None, env_file: Path | None, environ: Mapping[str, str] | None,
) -> tuple[Path, Path, Path, str]:
    """The build context, its compose file and the token the probe needs.

    Checked before anything is touched, because each of these missing means the
    rebuild would either start nothing or start it without auth.
    """
    source_path = source_dir(engine_root, source, environ=environ)
    compose_file = source_path / COMPOSE_FILE
    if not compose_file.is_file():
        raise DeployError(t(
            "{compose} is missing: the compose file that started this service has to sit beside its source "
            "(pass --source with the directory that holds both)",
            compose=compose_file,
        ))
    secrets_file = env_file or env_path(engine_root)
    token = secrets.read_env_file(secrets_file).get("VAULT_LIBRARY_TOKEN", "")
    if not token:
        raise DeployError(t(
            "no VAULT_LIBRARY_TOKEN in {file}: the service would refuse to start. Run bootstrap-vps.sh first",
            file=secrets_file,
        ))
    return source_path, compose_file, secrets_file, token


def deploy(
    engine_root: Path,
    *,
    source: Path | None = None,
    env_file: Path | None = None,
    state_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
    out: Callable[[str], None] = print,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_command,
    probe_fn: Callable[..., list[str]] = probe.probe,
    attempts: int = PROBE_ATTEMPTS,
    delay: float = PROBE_DELAY_SECONDS,
) -> DeployResult:
    """Rebuild `vault-mcp` from this engine, prove it answers, or put the old one back."""
    tree = engine_tree(engine_root)
    source_path, compose_file, secrets_file, token = _prepare(engine_root, source, env_file, environ)

    commit = _git_commit(tree, runner)
    short = alignment.short_commit(commit) or commit
    out(t("Deploying {service} from engine commit {commit}", service=SERVICE, commit=short))

    # Backup before the sync: what rollback must restore is the source as this
    # deploy found it, not the copy it is about to write.
    backup = _backup_source(source_path, state_dir or resolve_state_dir())
    out(t("  previous source saved in {dir}", dir=backup))
    _sync_source(tree, source_path, out)
    previous_image = _current_image(runner)
    if previous_image:
        runner(["docker", "tag", previous_image, f"{previous_image}{ROLLBACK_SUFFIX}"])
        out(t("  previous image kept as {tag}", tag=f"{previous_image}{ROLLBACK_SUFFIX}"))

    def undo() -> None:
        """The one rollback, used by every step that can fail below."""
        _roll_back(
            previous_image=previous_image, backup=backup, source=source_path, compose=compose_file,
            env_file=secrets_file, runner=runner, out=out,
        )

    try:
        _rebuild(compose_file, secrets_file, runner=runner, out=out, commit=commit)
    except DeployError:
        undo()
        raise
    tools = _wait_for_probe(
        address=published_url(), token=token, attempts=attempts, delay=delay, probe_fn=probe_fn, out=out,
    )
    if tools is None:
        undo()
        raise DeployError(t(
            "the rebuilt service did not answer the MCP protocol: {service} is back on the code that worked",
            service=SERVICE,
        ))

    out(t("  it answers MCP with {count} tool(s)", count=len(tools)))
    return DeployResult(
        commit=short, source=source_path, backup=backup,
        previous_image=previous_image, tools=tuple(tools),
    )


def _rebuild(
    compose: Path,
    env_file: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    out: Callable[[str], None],
    commit: str,
) -> None:
    """Build the image with its commit baked in, then start that one service.

    The service name is on both commands, so nothing else in the compose file is
    built, restarted or recreated: a deploy of the write door must never touch
    n8n.
    """
    out(t("  building {service}...", service=SERVICE))
    build = runner(_compose(compose, env_file, "build", SERVICE), env={COMMIT_ENV: commit})
    if build.returncode != 0:
        raise DeployError(t("the build failed: {detail}", detail=_last_line(build)))
    out(t("  starting {service}...", service=SERVICE))
    started = runner(_compose(compose, env_file, "up", "-d", "--no-build", SERVICE))
    if started.returncode != 0:
        raise DeployError(t("the service did not start: {detail}", detail=_last_line(started)))


def _wait_for_probe(
    *,
    address: str,
    token: str,
    attempts: int,
    delay: float,
    probe_fn: Callable[..., list[str]],
    out: Callable[[str], None],
) -> list[str] | None:
    """Ask the MCP protocol until it answers or the attempts run out."""
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            return probe_fn(address, token=token, timeout=probe.REQUEST_TIMEOUT_SECONDS)
        except probe.ProbeError as exc:
            last = str(exc)
        if attempt < attempts:
            time.sleep(delay)
    out(t("  the probe failed: {detail}", detail=last))
    return None

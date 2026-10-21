"""The executor's permissions, decided before its tab opens, and the answers it asks for.

One job: put the profile in the copy before a session starts, and speak to the
harness about the requests that still appear. The profile is a project config
file, because OpenCode 2 has no configuration environment variable: the engine
writes `opencode.json` in the copy, keeps every other key the owner put there,
and adds the file to the copy's own `info/exclude` so it never reaches a commit
or the review package. Nothing here auto-approves: edits stay inside the unit's
copy and the task folder, commands stay allowed, a push is denied, and whatever
the profile did not decide is not granted by the engine.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from nexgen_core.processes import windows_command_argv

from . import supervision_state as state
from .supervision_state import SupervisionError

#: The project config OpenCode reads from the working directory.
CONFIG_FILE = "opencode.json"
#: The three answers the harness accepts, as its own API names them.
DECISIONS = ("once", "always", "reject")
#: Why a request the profile did not pre-answer is refused instead of granted.
PROFILE_REFUSAL = (
    "fuori dal profilo scritto: la configurazione consente modifiche nella copia e nella "
    "cartella del compito, consente i comandi e nega la push; il resto non è concesso dal motore"
)
API_TIMEOUT_S = 120.0


class PermissionProfileError(SupervisionError):
    """The profile cannot be written, or the harness refused the question about a request."""


def profile_rules(copy: str | Path, task: str | Path) -> list[dict]:
    """The ordered rules the executor runs under; the last match wins.

    OpenCode matches an edit inside the session's own directory by its *relative*
    path (`docs/a.md`, seen in a real permission request) and a path outside it
    by its *canonical absolute* path (`C:/.../fuori/vietato.txt`, seen in
    another); a relative path that leaves the directory but stays in the same
    project worktree keeps its `..` form. So the rules allow the relative form,
    deny every absolute form by its drive letter, deny the `..` form, and then
    reopen exactly the task folder, which is where the unit writes its report.
    A real tab proved all four outcomes; see the report. Commands are allowed
    except a push in its three spellings; the copy's own pushurl remains the
    hard stop behind this one, because a shell carries the user's authority.
    """
    return [
        {"action": "edit", "resource": _inside_glob(), "effect": "allow"},
        {"action": "edit", "resource": _drive_glob(), "effect": "deny"},
        {"action": "edit", "resource": _rooted_glob(), "effect": "deny"},
        {"action": "edit", "resource": _parent_glob(), "effect": "deny"},
        {"action": "edit", "resource": _folder_glob(task), "effect": "allow"},
        {"action": "shell", "resource": "git push *", "effect": "deny"},
        {"action": "shell", "resource": "git -C * push *", "effect": "deny"},
        {"action": "shell", "resource": "git -c * push *", "effect": "deny"},
    ]


def write_profile(task_id: str, copy: str | Path) -> Path:
    """Write the executor's profile into the unit's copy and keep it out of git.

    The owner's other keys are preserved: the model of the copy stays the one
    the owner chose. A config that is tracked by the project is refused instead
    of overwritten, because changing it would put a foreign file in the review.
    """
    folder = Path(copy)
    if not folder.is_dir():
        raise PermissionProfileError(f"copia dell'unità inesistente: {folder}")
    target = folder / CONFIG_FILE
    if _tracked(folder, target.name):
        raise PermissionProfileError(
            f"il progetto traccia {target.name}: il profilo dell'esecutore non può stare lì "
            "senza sporcare il diff (togli il file dal progetto o usa un'altra copia)"
        )
    current = _read_config(target)
    current["permissions"] = profile_rules(folder, state.open_task(task_id))
    current.setdefault("$schema", "https://opencode.ai/config.json")
    _atomic_json(target, current)
    _ignore(folder, target.name)
    return target


def pending(session: str) -> list[dict]:
    """The permission requests this session is waiting on, as the harness lists them."""
    payload = _api("session.permission.list", [f"sessionID={session}"])
    items = payload.get("data")
    if not isinstance(items, list):
        raise PermissionProfileError(f"la lista delle richieste non è una lista: {payload!r:.200}")
    return [item for item in items if isinstance(item, dict)]


def answer(session: str, request_id: str, decision: str) -> dict:
    """Reply to one request, with the harness's own decision name, and return its answer."""
    choice = (decision or "").strip()
    if choice not in DECISIONS:
        raise PermissionProfileError(
            f"decisione non valida: {decision!r} (attese: {', '.join(DECISIONS)})"
        )
    request = (request_id or "").strip()
    if not request:
        raise PermissionProfileError("richiesta vuota: indica l'identificativo della richiesta")
    payload = _api(
        "session.permission.reply",
        [f"sessionID={session}", f"requestID={request}"],
        data={"decision": choice},
    )
    problem = str(payload.get("_tag") or "")
    if problem.endswith("Error"):
        raise PermissionProfileError(
            f"la richiesta {request} non è stata accettata: {problem}"
        )
    return payload


def summary(item: dict, limit: int = 240) -> str:
    """One request in one bounded line, without guessing a shape the harness did not promise."""
    text = json.dumps(item, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _api(operation: str, params: list[str], *, data: dict | None = None) -> dict:
    """One call to the running harness, parsed as JSON; a refusal names itself."""
    argv = ["opencode", "api", operation]
    for param in params:
        argv.extend(["--param", param])
    if data is not None:
        argv.extend(["--data", json.dumps(data, ensure_ascii=False)])
    try:
        completed = subprocess.run(
            windows_command_argv(argv), capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, timeout=API_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PermissionProfileError(
            f"`opencode api {operation}` non eseguibile ({type(exc).__name__})"
        ) from exc
    if completed.returncode:
        detail = ((completed.stdout or "") + (completed.stderr or "")).strip()
        raise PermissionProfileError(
            f"`opencode api {operation}` esce con {completed.returncode}: {detail[:300]}"
        )
    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        raise PermissionProfileError(f"`opencode api {operation}` non è JSON") from exc
    if not isinstance(payload, dict):
        raise PermissionProfileError(f"`opencode api {operation}` non è un oggetto JSON")
    return payload


def _read_config(path: Path) -> dict:
    """The config as it is, or an empty one; a file that does not parse is never overwritten."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:  # nexgen: allow reason="una copia nuova non ha ancora un file di configurazione, e il profilo lo crea"
        return {}
    except OSError as exc:
        raise PermissionProfileError(
            f"configurazione dell'esecutore non leggibile: {path} ({exc})"
        ) from exc
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise PermissionProfileError(
            f"{path.name} non è JSON: il profilo non lo sovrascrive ({path})"
        ) from exc
    if not isinstance(parsed, dict):
        raise PermissionProfileError(f"{path.name} non è un oggetto: il profilo non lo sovrascrive")
    return parsed


def _atomic_json(path: Path, payload: dict) -> None:
    """Replace the file in one step, so a reader never sees half a profile."""
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _tracked(folder: Path, name: str) -> bool:
    """Whether git already tracks this file in the copy."""
    try:
        done = subprocess.run(
            ["git", "-C", str(folder), "ls-files", "--error-unmatch", name],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError as exc:
        raise PermissionProfileError(f"git non eseguibile nella copia: {folder} ({exc})") from exc
    return done.returncode == 0


def _ignore(folder: Path, name: str) -> tuple[bool, str]:
    """Add the profile to the copy's exclude file, exactly once; say where it went."""
    try:
        done = subprocess.run(
            ["git", "-C", str(folder), "rev-parse", "--git-path", "info/exclude"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError as exc:
        raise PermissionProfileError(f"git non eseguibile nella copia: {folder} ({exc})") from exc
    if done.returncode or not done.stdout.strip():
        if _has_git(folder):
            raise PermissionProfileError(
                f"git non risolve `info/exclude` in {folder}: {name} resterebbe fuori "
                "dall'esclusione della copia"
            )
        return False, "cartella senza git: niente diff da proteggere"
    path = Path(done.stdout.strip())
    if not path.is_absolute():
        path = (folder / path).resolve()
    line = f"/{name}"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if line in {row.strip() for row in text.splitlines()}:
        return True, str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        if text and not text.endswith("\n"):
            handle.write("\n")
        handle.write(f"{line}\n")
    return True, str(path)


def _has_git(folder: Path) -> bool:
    """Whether this folder is a repository or a worktree, by the marker git itself writes."""
    return (folder / ".git").exists()


def _inside_glob() -> str:
    """Everything the session reaches by a relative path, which is the unit's copy.

    A real permission request for a file in the copy asked for `docs/a.md`, never
    for its absolute path, so `*` here is the copy and nothing else: the two
    denies below take away the forms that leave it.
    """
    return "*"


def _drive_glob() -> str:
    """Every absolute path on a Windows drive: one letter, a colon, a slash.

    A relative path cannot look like this (a colon is not a valid character in a
    file name), so this is exactly "outside the copy, canonicalised".
    """
    return "?:/*"


def _rooted_glob() -> str:
    """Every absolute path that starts at a root, which on Windows is a UNC share."""
    return "/*"


def _parent_glob() -> str:
    """A relative path that leaves the copy, which is the owner's checkout."""
    return "../*"


def _folder_glob(target: str | Path) -> str:
    """One folder outside the copy, as the absolute pattern the harness matches.

    The task folder sits beside the lane's state, outside the repository, so the
    harness compares it in its canonical absolute form; the slash-separated
    spelling is the one the permission request showed.
    """
    return Path(target).resolve().as_posix() + "/*"

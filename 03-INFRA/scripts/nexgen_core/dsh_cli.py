"""Build and read one DeepSeek Harness (``dsh``) run, for every caller that starts one.

One job: everything the Harness launcher itself dictates -- where it is installed, the per-call
patch that pins the model route and turns its three uploads off, the space-free wrapper Windows
needs, and the NDJSON stream it answers in -- because the Council seat and the ``sup`` supervisor
both start dsh and must agree on every one of those facts, and a second copy of them drifts.

Nothing here knows about the Council: the private-directory writer arrives as a parameter, and
this is the engine's own package.

Verified live on 2026-11-14 against DeepSeek Harness 0.2.2-rc.3 on Windows:
``dsh --profile headless --patch <file> --permission-mode read-only --approval-policy never
--json -`` with the brief on stdin exits 0 and prints ``session``, ``status``
(turn_start/step_start/step_end/turn_end), ``text`` and ``final`` events. An unanswered turn
exits 1 with ``dsh: <CODE>: <message>`` on stderr and a ``final`` event whose reason is an
error, so a caller classifies a non-zero exit before it reads the stream.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import yaml

from nexgen_core import deposit_env

#: The profile the shipped app materializes for a one-shot run (``resources/cli`` boots it).
HEADLESS_PROFILE = "headless"
#: The single variable the harness reads for both OpenCode routes.
HARNESS_KEY_ENV = "OPENCODE_API_KEY"
#: Harness route -> the name this machine stores that plan's key under. A route keeps the
#: engine's own `provider/model` vocabulary, and the harness has one variable for both routes,
#: so the route decides which plan's key is bridged over.
KEY_SOURCES = {"opencode-go": "OPENCODE_GO_API_KEY", "opencode": "OPENCODE_ZEN_API_KEY"}
#: Where the Windows installer puts the launcher, relative to ``%LOCALAPPDATA%``: the app ships
#: dsh inside ``resources`` and never puts it on PATH.
WINDOWS_LAUNCHER = (
    Path("Programs") / "DeepSeek Harness" / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd"
)


class DshError(RuntimeError):
    """A dsh run that cannot be built or read."""


def executable() -> str | None:
    """The launcher on this host, or None when the harness is not installed here.

    PATH first (an npm install exposes ``dsh``), then the installer's own location, which is
    where the app keeps it and the only place it exists on a normal Windows install.
    """
    found = shutil.which("dsh")
    if found:
        return found
    if os.name != "nt":
        return None
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return None
    candidate = Path(local_app_data) / WINDOWS_LAUNCHER
    return str(candidate) if candidate.is_file() else None


def split_route(model: str) -> tuple[str, str]:
    """``provider/model`` as the harness wants it: (route, provider-owned model id).

    The harness addresses a model by route and id separately and has no ``--model`` flag, so a
    caller declares ``provider/model`` like the engine's other CLIs do and the two halves travel
    in the patch instead of in argv.
    """
    provider, _, name = str(model or "").partition("/")
    if not provider.strip() or not name.strip():
        raise DshError(f"a dsh route must be <provider>/<model>, got {model!r}.")
    return provider.strip(), name.strip()


def credential_names(provider: str) -> tuple[str, ...]:
    """Where to look for this route's key, in order: the plan it names, then the harness's name.

    The harness documents one variable for both OpenCode routes, and a launcher that starts a
    dsh run from a desktop app may hold only that one. A route this module does not know still
    gets the harness's own name rather than an empty bridge.
    """
    plan = KEY_SOURCES.get(provider)
    return (plan, HARNESS_KEY_ENV) if plan else (HARNESS_KEY_ENV,)


def _first_value(names: tuple[str, ...], values: dict[str, str]) -> str:
    """The first of these names that carries a value, or an empty string."""
    for name in names:
        value = str(values.get(name) or "").strip()
        if value:
            return value
    return ""


def credential_value(provider: str) -> str:
    """This route's key from the environment, else from the secrets deposit, else "".

    Nothing loads the deposit into a graphical session or a service (see ``deposit_env``), so a
    run started from one would otherwise start without a key it already has on disk. An empty
    answer is deliberate: the harness then fails with its own ``MISSING_CREDENTIAL``, which is a
    better diagnostic than a bridge that carries nothing.
    """
    names = credential_names(provider)
    found = _first_value(names, os.environ)
    return found or _first_value(names, deposit_env.read_deposit())


def patch_document(
    provider: str, model: str, *, effort: str = "", data_root: Path | None = None
) -> list[dict]:
    """The per-call overlay: pinned route, optional private data root, and the three uploads off.

    Three harness defaults would be wrong for a run that receives a private brief. The
    session-log plugin appends up to 8 MiB of the session to requests made to the official
    DeepSeek API, the session-telemetry exporter ships session history to DeepSeek's collector,
    and the package inventory sends the active plugin list with the same requests. The brief
    leaves this machine only to the route a caller declares, so all three are disabled here.
    They are config keys, not flags, so this document is the only place they can be set.

    ``data_root`` points the session log and the projection store at a private directory.
    Without it those keys are left out, so the harness keeps them where the person's own run
    keeps them and the sessions stay visible in the app. The launcher resolves its home from
    ``DSH_HOME`` (the caller's job): a run with that variable set materializes a fresh copy of
    the shipped ``headless`` profile there and the person's ``~/.dsh`` gains nothing.

    The reasoning effort rides here too: ``agent-default-model`` is the harness's only model
    control, and a value the route does not know is its own to reject.
    """
    selection: dict[str, str] = {"provider": provider, "model": model}
    wanted = str(effort or "").strip()
    if wanted and wanted != "none":
        selection["reasoningEffort"] = wanted
    document = [
        # A bare pi-ai mount registers nothing until a route is declared: this is that route.
        {"id": "llm-pi-ai", "config": {"providers": {provider: {"apiKeyEnv": HARNESS_KEY_ENV}}}},
        {"id": "agent-default-model", "config": selection},
    ]
    if data_root is not None:
        document += [
            {"id": "session-persistence-jsonl", "config": {"root": str(data_root / "sessions")}},
            {"id": "storage-json", "config": {"root": str(data_root / "storages")}},
        ]
    document += [
        {"id": "session-log-deepseek", "config": {"enabled": False}},
        {"id": "session-telemetry-otel", "config": {"mode": "DISABLED"}},
        {"id": "plugin-package-inventory-deepseek", "config": {"enabled": False}},
    ]
    return document


def patch_text(document: list[dict]) -> str:
    """The patch file's text: the entries in the order the document built them."""
    return yaml.safe_dump(document, sort_keys=False)


def launcher(scratch: Path, writer) -> str:
    """The launcher to start on this host: the vendor's own, or a space-free wrapper for it.

    Windows starts a batch file only through ``cmd.exe``, and the engine's shared launcher
    helper hands ``cmd.exe`` the path as a separate argument: a batch path with a space in it
    (``...\\Programs\\DeepSeek Harness\\...\\dsh.cmd``) is split at the space and never runs.
    Inside a batch file a quoted path needs no escaping, so one generated line keeps the
    vendor's launcher authoritative and works. Only a launcher under a path with a space gets
    the wrapper, so the ordinary road is untouched; ``scratch`` is the private directory the
    wrapper lives in, and ``writer(path, text)`` is the caller's private-text writer. The
    engine's writer opens in text mode, so the line's ``\\n`` reaches a Windows batch file as
    CRLF, which is the terminator it wants.
    """
    vendor = executable() or "dsh"
    if os.name != "nt" or " " not in vendor or not vendor.casefold().endswith((".cmd", ".bat")):
        return vendor
    path = scratch / "dsh-launch.cmd"
    writer(path, f'@call "{vendor}" %*\n')
    return str(path)


def read_events(raw: str) -> list[dict]:
    """The harness's NDJSON stream as events, skipping blank lines and a tail cut mid-line."""
    events: list[dict] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:  # nexgen: allow reason="a run killed mid-line leaves a partial tail, and the missing final event is what refuses the answer"
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def final_event(events: list[dict]) -> dict | None:
    """The last ``final`` event the harness wrote, or None when the stream stopped short."""
    finals = [event for event in events if event.get("type") == "final"]
    return finals[-1] if finals else None


def session_id(events: list[dict]) -> str:
    """The id the harness gave this run: the first ``session`` event's, else the final's."""
    for event in events:
        if event.get("type") == "session":
            value = event.get("sessionId")
            if isinstance(value, str) and value.strip():
                return value
    final = final_event(events)
    value = final.get("sessionId") if final else None
    return value if isinstance(value, str) else ""


def _step_tokens(event: dict) -> int:
    """One step's reported total, or 0 when the harness omitted a partial sum."""
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return 0
    total = usage.get("totalTokens")
    return total if isinstance(total, int) and not isinstance(total, bool) else 0


def total_tokens(events: list[dict]) -> int:
    """Tokens the harness reported, summed over the steps it finished.

    The harness records no cost anywhere, so a recap can print tokens and must not invent a
    price.
    """
    return sum(
        _step_tokens(event)
        for event in events
        if event.get("type") == "status" and event.get("phase") == "step_end"
    )

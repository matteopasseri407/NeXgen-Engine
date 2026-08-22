"""The verbs that act on the engine: align, diagnose, update.

Every function here receives already-parsed arguments and returns an exit
code. None of them decide how to format output: formatting is a decision
made exactly once, at the edge, in `__init__.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

from nexgen_core.i18n import t
from nexgen_core.paths import (
    remotes_config,
    resolve_engine_root,
    resolve_home,
    resolve_vault_data,
)


def _all(args) -> list[str]:
    """The arguments the dispatcher didn't recognize, in their original order."""
    return list(getattr(args, "passthrough", []) or [])


def register(sub) -> None:
    """Declares the engine's verbs on the dispatcher."""
    p = sub.add_parser("sync", aliases=["apply"], help=t("Align this machine now"))
    p.add_argument("--offline", "--allow-offline", dest="allow_offline", action="store_true",
                   help=t("Proceed even without reaching the remote"))
    p.add_argument("--skip-mcp", action="store_true",
                   help=t("Don't regenerate the connector configurations"))
    p.set_defaults(func=cmd_sync, mode="apply")

    p = sub.add_parser("guard", help=t("The recurring alignment cycle (never publishes)"))
    p.add_argument("--offline", "--allow-offline", dest="allow_offline", action="store_true")
    p.add_argument("--skip-mcp", action="store_true")
    p.set_defaults(func=cmd_sync, mode="guard")

    p = sub.add_parser("pull", help=t("Download the data without regenerating derived files"))
    p.add_argument("--offline", "--allow-offline", dest="allow_offline", action="store_true")
    p.set_defaults(func=cmd_sync, mode="pull", skip_mcp=False)

    p = sub.add_parser("preflight", help=t("Check the configuration without writing anything"))
    p.set_defaults(func=cmd_sync, mode="preflight", allow_offline=False, skip_mcp=False)

    p = sub.add_parser("doctor", help=t("Tell me if something's wrong"))
    p.add_argument("-v", "--verbose", action="store_true", help=t("List everything that was checked"))
    p.add_argument("--strict", action="store_true", help=t("Also treat undetermined results as failures"))
    p.add_argument("--json", action="store_true", help=t("Output in JSON format"))
    p.add_argument("--summary", action="store_true", help=t("A one-line summary"))
    p.add_argument("--fix", "--remedy", dest="fix", action="store_true",
                   help=t("Apply the available automatic remedies"))
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("update", help=t("Update the engine, with confirmation"))
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("upgrades", help=t("Show me what's moved upstream"))
    p.set_defaults(func=cmd_upgrades)

    p = sub.add_parser("inventory", help=t("What's installed on this machine, without touching anything"))
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("config", help=t("Show the resolved remote configuration"))
    p.add_argument("field", choices=["authoritative_remote", "mirrors"])
    p.add_argument("value", nargs="?", default=None, help=t("New value; writes remotes.yaml"))
    p.set_defaults(func=cmd_config)

    # Internal verbs: invoked by timers, not by people.
    p = sub.add_parser("heartbeat", help=t("Liveness beat and maintenance tasks (internal use)"))
    p.set_defaults(func=cmd_heartbeat)

    p = sub.add_parser("notify-failure", help=t("Alert for a guard unit that failed to start (internal use)"))
    p.add_argument("unit", nargs="?", default=t("a guard unit"))
    p.set_defaults(func=cmd_notify_failure)

    p = sub.add_parser("bootstrap-alerts", help=t("Diagnose and alert only on failures (internal use)"))
    p.set_defaults(func=cmd_bootstrap_alerts)


def _action_mark(act: str) -> tuple[str, str]:
    """The mark and the text for one action line, from its own prefix.

    A line that reports a failure must not be printed with the mark that
    means it worked: three skills that failed to clone came out under a
    green tick, next to the things that actually happened. A warning is
    neither: it gets a mark of its own, so a green tick keeps meaning
    "this worked".
    """
    if act.startswith(("[ERROR]", "[ERRORE]")):
        return "✗", act.split("] ", 1)[-1]
    if act.startswith(("[WARN]", "[AVVISO]")):
        return "!", act.split("] ", 1)[-1]
    return "✓", act


def cmd_sync(args) -> int:
    from nexgen_core.guard import GuardMode, GuardRunner

    runner = GuardRunner()
    res = runner.run(
        mode=GuardMode(args.mode),
        allow_offline=getattr(args, "allow_offline", False),
        skip_mcp=getattr(args, "skip_mcp", False),
    )
    for act in res.actions_taken:
        mark, text = _action_mark(act)
        print(f"  {mark} {text}")
    print(res.message)
    return res.exit_code


def cmd_doctor(args) -> int:
    from nexgen_core.doctor import Doctor

    report = Doctor().run_diagnostics(apply_remedies=args.fix)
    if args.json:
        print(report.format_json())
    elif args.summary:
        print(f"FAIL={len(report.broken)} OK={report.ok_count} WARN={len(report.warnings)} UNDETERMINED={len(report.undetermined)}")
    else:
        print(report.format_human(verbose=args.verbose))
    return report.exit_code(strict=args.strict)


def cmd_update(args) -> int:
    from nexgen_core.updater import main as updater_main

    return updater_main(_all(args))


def cmd_upgrades(args) -> int:
    from nexgen_core.updater import EngineUpdater

    return EngineUpdater.main(["--check"])


def cmd_config(args) -> int:
    from nexgen_core.git_ops import resolve_remotes

    vault_data = resolve_vault_data()
    if getattr(args, "value", None) is not None:
        if args.field != "authoritative_remote":
            print(t(
                "Only 'authoritative_remote' can be set from the command line; "
                "mirrors are edited in remotes.yaml.",
            ), file=sys.stderr)
            return 2
        return _set_authoritative_remote(vault_data, args.value)

    auth_remote, mirrors = resolve_remotes(vault_data)
    print(auth_remote if args.field == "authoritative_remote" else "\n".join(mirrors))
    return 0


def _set_authoritative_remote(vault_data: Path, remote: str) -> int:
    """Names the authoritative remote in sync/remotes.yaml, keeping the rest.

    The command exists because the installer's own message says so: a
    single-machine install that named the wrong remote has no other way to
    fix it. The file is the vault's content, so the write is left uncommitted
    on purpose: publishing is a decision made by a separate command.
    """
    path = remotes_config(vault_data)
    data: dict = {}
    if path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, yaml.YAMLError):
            data = {}
    data["schema_version"] = data.get("schema_version", 1)
    data["authoritative_remote"] = remote.strip()
    data.setdefault("mirrors", [])
    body = (
        "# Written by the installer. `nexgen config authoritative_remote <name>`\n"
        "# changes it; see docs/sync-contract.md for what the two fields mean.\n"
        + yaml.safe_dump(data, sort_keys=False)
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        print(t("Could not write {path}: {error}", path=path, error=exc), file=sys.stderr)
        return 1
    print(t("Authoritative remote set to '{remote}'.", remote=remote.strip()))
    print(t(
        "The vault now has uncommitted changes; publish them with "
        "'nexgen vault push {file}'.",
        file="03-INFRA/agent-universal-layer/sync/remotes.yaml",
    ))
    return 0


def cmd_heartbeat(args) -> int:
    from nexgen_core.beat import Heartbeat

    res = Heartbeat().run_beat()
    status = t("active") if res["liveness_ok"] else t("stalled")
    print(t("Heartbeat: {status} ({message})", status=status, message=res["liveness_msg"]))
    return 0 if res["liveness_ok"] else 1


def cmd_notify_failure(args) -> int:
    from nexgen_core.megaphone import Megaphone

    unit = args.unit
    summary = t(
        "{unit} did not start, and until it does this machine stops staying aligned. "
        "Check it with: systemctl --user status {unit}",
        unit=unit,
    )
    if not Megaphone().send_alert(
        title=t("The guard did not start"),
        message=summary,
        action=f"systemctl --user status {unit}",
        alert_key=f"notify-failure-{unit}",
    ):
        print(t("notify-failure: {summary} (no alert channel configured)", summary=summary), file=sys.stderr)
    return 0


def cmd_bootstrap_alerts(args) -> int:
    from nexgen_core.doctor import Doctor
    from nexgen_core.megaphone import Megaphone

    report = Doctor().run_diagnostics(apply_remedies=False)
    if report.has_failures:
        Megaphone().send_alert(
            title=t("There are problems on this machine"),
            message="; ".join(o.message for o in report.broken[:5]),
            action="nexgen doctor",
            alert_key="bootstrap-alerts-doctor",
        )
    print(t("Diagnostics complete (failures={failures}, ok={ok}).", failures=len(report.broken), ok=report.ok_count))
    return report.exit_code()


def cmd_inventory(args) -> int:
    """What's actually on this machine, compared against what should be there."""
    from nexgen_core.renderer import McpRenderer
    from nexgen_core.skills import SkillMaterializer

    home = resolve_home()
    vault_data = resolve_vault_data(home)
    engine_root = resolve_engine_root(home)
    problems = 0

    print(t(">>> MCP connectors per runtime"))
    renderer = McpRenderer(vault_data=vault_data, engine_root=engine_root, home=home)
    for cli_name in ("claude", "codex", "antigravity", "opencode"):
        servers = renderer.load_resolved_servers(cli_name)
        names = ", ".join(sorted(servers)) if servers else t("none")
        print(f"  {cli_name}: {len(servers)} — {names}")

    print("\n" + t(">>> Skills: manifest compared against the materialized library"))
    mat = SkillMaterializer(vault_data=vault_data, engine_root=engine_root, home=home)
    declared = mat.load_manifest()
    materialized = [p.name for p in mat.library_dir.iterdir() if p.is_dir()] if mat.library_dir.is_dir() else []
    extras = sorted(m for m in materialized if m not in declared)
    missing = sorted(s for s in declared if s not in materialized)
    print(t("  {materialized} materialized, {declared} declared", materialized=len(materialized), declared=len(declared)))
    if extras:
        print(t("  outside the manifest (kept, never deleted): {names}", names=", ".join(extras)))
    if missing:
        print(t("  declared but not materialized yet: {names}", names=", ".join(missing)))
        problems += 1

    print("\n" + t(">>> Instructions per runtime"))
    for label, path, kind in _bootstrap_targets(home):
        state = _instruction_state(path, kind, vault_data)
        print(f"  {label}: {state}")
        if state.startswith("diverged"):
            problems += 1

    print("\n" + t(">>> Runtimes' native memories"))
    for label, note in _native_memory_report(home):
        print(f"  {label}: {note}")

    print("\n" + t("Read-only: nothing was modified."))
    return 2 if problems else 0


def _bootstrap_targets(home: Path) -> list[tuple[str, Path, str]]:
    return [
        ("claude", home / "CLAUDE.md", "pointer"),
        ("codex", home / ".codex" / "AGENTS.md", "mirror"),
        ("antigravity", home / ".gemini" / "config" / "AGENTS.md", "mirror"),
    ]


def _instruction_state(path: Path, kind: str, vault_data: Path) -> str:
    """Says not just whether the file is there, but whether it still tells the truth.

    "Present" isn't enough: a real copy that has stopped matching the
    canonical file is exactly the case this inventory needs to catch.
    """
    import hashlib

    from nexgen_core.paths import canonical_instructions

    canon = canonical_instructions(vault_data)
    if not path.exists() and not path.is_symlink():
        return "absent"
    if path.is_symlink():
        return f"link -> {path.resolve()}"
    try:
        body = path.read_bytes()
    except OSError as exc:
        return f"unreadable ({exc})"
    if kind == "pointer":
        return "pointer" if str(canon) in body.decode("utf-8", "replace") else "diverged: does not name the canonical file"
    if not canon.is_file():
        return "real copy (canonical file doesn't exist, can't compare)"
    same = hashlib.sha256(body).hexdigest() == hashlib.sha256(canon.read_bytes()).hexdigest()
    return "copy aligned" if same else "diverged: real copy differs from the canonical file"


def _native_memory_report(home: Path) -> list[tuple[str, str]]:
    """The memories each runtime keeps on its own, alongside the Vault.

    This isn't a diagnosis: it's a census, because onboarding needs to be
    able to tell the user what already exists before proposing to adopt or
    reset it.
    """
    out: list[tuple[str, str]] = []

    claude_memory = home / ".claude" / "memory"
    if claude_memory.is_dir():
        facts = sum(1 for _ in claude_memory.rglob("*.md"))
        out.append(("claude", t("{count} durable facts in {path}", count=facts, path=claude_memory)))
    else:
        out.append(("claude", t("no native memory")))

    for label, path in (
        ("codex", home / ".codex" / "sessions"),
        ("opencode", home / ".opencode" / "storage"),
        ("antigravity", home / ".gemini" / "tmp"),
    ):
        if path.is_dir():
            count = sum(1 for _ in path.rglob("*") if _.is_file())
            out.append((label, t(
                "{count} transcript files in {path} (to be distilled, not structured memory)",
                count=count, path=path,
            )))
        else:
            out.append((label, t("no transcripts")))

    return out

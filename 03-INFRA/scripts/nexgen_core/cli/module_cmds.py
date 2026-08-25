"""The verbs on modules: see what the engine is made of, declare its state.

The installer stays AI-agent-driven: the agent interviews the user with
multiple-choice questions and runs these deterministic commands underneath.
`modules list` is the read-only inventory; `modules set` is the only way to
change a module's state, and it refuses anything the catalog does not
support, so the agent can never improvise a state that does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.modules import (
    MODULE_MANIFEST,
    MODULE_STATES,
    current_host,
    external_paths,
    load_catalog,
    load_external_module,
    resolve_vault_data,
    write_state_file,
)
from nexgen_core.paths import resolve_engine_root


def register(sub) -> None:
    p = sub.add_parser("modules", help=t("The engine's modules: what is installed where"))
    ssub = p.add_subparsers(dest="modules_command", metavar="verb")

    q = ssub.add_parser("list", help=t("Inventory: every module and its state on this machine"))
    q.set_defaults(func=cmd_list)

    q = ssub.add_parser("set", help=t("Declare one module's state (absent|local|remote)"))
    q.add_argument("module", help=t("Module id from 'modules list'"))
    q.add_argument("state", choices=MODULE_STATES, help=t("The state to declare"))
    scope_group = q.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--here", action="store_true",
        help=t("Force the declaration to this machine only, whatever the module declares."),
    )
    scope_group.add_argument(
        "--everywhere", action="store_true",
        help=t("Force the declaration onto every machine that shares the vault."),
    )
    q.set_defaults(func=cmd_set)

    q = ssub.add_parser(
        "add",
        help=t("Pick up a repository that declares itself a module"),
        description=t("A directory containing {manifest} is a module. Adding it registers it for "
                      "THIS machine only, because a checkout exists where it exists.", manifest=MODULE_MANIFEST),
    )
    q.add_argument("path", help=t("Path to the module repository"))
    q.set_defaults(func=cmd_add)

    q = ssub.add_parser("remove", help=t("Stop picking up an external module on this machine"))
    q.add_argument("path", help=t("Path previously given to 'modules add'"))
    q.set_defaults(func=cmd_remove)

    p.set_defaults(func=lambda a: p.print_help() or 0)


def cmd_list(args) -> int:
    from nexgen_core.modules import modules_state

    states = modules_state()
    if not states:
        print(t("No modules found: the catalog is missing or unreadable."))
        return 1
    print(f"{'module':<14} {'kind':<10} {'state':<8} {'origin':<9} {'scope':<7} note")
    print("-" * 92)
    for item in states:
        origin = "external" if item.module.source else "engine"
        print(f"{item.module.id:<14} {item.module.kind:<10} {item.state:<8} "
              f"{origin:<9} {item.module.scope:<7} {item.note}")
    print()
    print(t("Host: {host}", host=current_host()))
    print(t("Declare or change states with 'nexgen modules set <module> <state>'; "
            "possible states per module are listed by 'nexgen modules list --all'."))
    return 0


def _vault(args) -> Path:
    from nexgen_core.paths import resolve_vault_data as _resolve
    if getattr(args, "vault_data", None):
        return resolve_vault_data(None, Path(args.vault_data))
    return _resolve()


def cmd_add(args) -> int:
    repo = Path(args.path).expanduser().resolve()
    try:
        module = load_external_module(repo)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    path = write_state_file(_vault(args), add_external=str(repo))
    print(t("module '{module}' picked up from {repo} for host {host} ({path})",
            module=module.id, repo=repo, host=current_host(), path=path))
    scope_note = (t("this machine only") if module.scope == "host" else t("every machine sharing the vault"))
    print(t("scope: {scope} ({note})", scope=module.scope, note=scope_note))
    print(t("It stays absent until you declare it: nexgen modules set {module} local", module=module.id))
    return 0


def cmd_remove(args) -> int:
    repo = str(Path(args.path).expanduser().resolve())
    vault = _vault(args)
    if repo not in external_paths(vault):
        print(t("{repo} is not registered on this host", repo=repo), file=sys.stderr)
        return 2
    path = write_state_file(vault, remove_external=repo)
    print(t("no longer picked up: {repo} ({path})", repo=repo, path=path))
    return 0


def cmd_set(args) -> int:
    engine_root = resolve_engine_root()
    vault_data = _vault(args)
    modules = load_catalog(engine_root, external=external_paths(vault_data))
    module = modules.get(args.module)
    if module is None:
        known = ", ".join(sorted(modules))
        print(t("unknown module '{module}'. Known: {known}", module=args.module, known=known), file=sys.stderr)
        return 2
    if not module.supports(args.state):
        print(
            t("module '{module}' does not support state '{state}'. Supported: {supported}",
              module=args.module, state=args.state, supported=", ".join(module.states)),
            file=sys.stderr,
        )
        return 2

    # The module says where its declaration belongs: whether a thing can run on
    # any machine is a property of the thing, not of whoever types the command.
    # The flags are an override for the case the author did not foresee.
    if args.here:
        host = current_host()
    elif args.everywhere:
        host = None
    else:
        host = current_host() if module.scope == "host" else None
    try:
        path = write_state_file(vault_data, module=args.module, state=args.state, host=host)
    except Exception as exc:
        print(t("Could not write the state: {error}", error=exc), file=sys.stderr)
        return 1
    if host:
        why = t("declared scope: host") if not args.here else t("forced with --here")
        print(t("(this machine only: {host} - {why})", host=host, why=why))
    print(t("module '{module}' set to '{state}' ({path})", module=args.module, state=args.state, path=path))
    return 0

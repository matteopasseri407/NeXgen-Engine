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
from nexgen_core.modules import MODULE_STATES, load_catalog, load_state_file, resolve_vault_data
from nexgen_core.paths import resolve_engine_root


def register(sub) -> None:
    p = sub.add_parser("modules", help=t("The engine's modules: what is installed where"))
    ssub = p.add_subparsers(dest="modules_command", metavar="verb")

    q = ssub.add_parser("list", help=t("Inventory: every module and its state on this machine"))
    q.set_defaults(func=cmd_list)

    q = ssub.add_parser("set", help=t("Declare one module's state (absent|local|remote)"))
    q.add_argument("module", help=t("Module id from 'modules list'"))
    q.add_argument("state", choices=MODULE_STATES, help=t("The state to declare"))
    q.set_defaults(func=cmd_set)

    p.set_defaults(func=lambda a: p.print_help() or 0)


def cmd_list(args) -> int:
    from nexgen_core.modules import modules_state

    states = modules_state()
    if not states:
        print(t("No modules found: the catalog is missing or unreadable."))
        return 1
    print(f"{'module':<14} {'kind':<10} {'state':<8} note")
    print("-" * 78)
    for item in states:
        note = item.note
        print(f"{item.module.id:<14} {item.module.kind:<10} {item.state:<8} {note}")
    print()
    print(t("Declare or change states with 'nexgen modules set <module> <state>'; "
            "possible states per module are listed by 'nexgen modules list --all'."))
    return 0


def cmd_set(args) -> int:
    engine_root = resolve_engine_root()
    vault_data = resolve_vault_data(None, Path(args.vault_data)) if getattr(args, "vault_data", None) else None
    from nexgen_core.paths import resolve_vault_data as _resolve_vault_data
    if vault_data is None:
        vault_data = _resolve_vault_data()

    modules = load_catalog(engine_root)
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

    path = vault_data / "03-INFRA" / "agent-universal-layer" / "modules.state.yaml"
    try:
        current = load_state_file(vault_data)
    except Exception as exc:
        print(t("Could not read the current state: {error}", error=exc), file=sys.stderr)
        return 1
    current[args.module] = args.state
    body = (
        "# Per-machine module state, written by 'nexgen modules set'.\n"
        "# Declares where each module runs: absent (off), local (this machine),\n"
        "# remote (a machine you own, reached over a tunnel).\n"
        "schema_version: 1\n\nmodules:\n"
        + "\n".join(f"  {mid}: {state}" for mid, state in sorted(current.items()))
        + "\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        print(t("Could not write {path}: {error}", path=path, error=exc), file=sys.stderr)
        return 1
    print(t("module '{module}' set to '{state}' ({path})", module=args.module, state=args.state, path=path))
    return 0

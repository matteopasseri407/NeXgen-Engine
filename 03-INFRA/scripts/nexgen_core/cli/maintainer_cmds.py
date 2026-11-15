"""The `nexgen maintainer` verbs: is this installation registered, and registering it.

Status is the diagnostic every refusal points at, so it answers on every installation, maintainer
or not. Register writes the flag into this installation's private config with the same reader and
writer `nexgen config` uses, and then reports whether the key half is satisfied: a flag without a
listed signing key authorises nothing.

One job: render one `MaintainerStatus`, and write the flag that asks for the tools.
"""
from __future__ import annotations

import json
import sys

import yaml

from nexgen_core import maintainer
from nexgen_core.cli.remotes_file import RemotesFileError, read_remotes_file
from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.marks import safe_mark


def _lines(status: maintainer.MaintainerStatus) -> list[str]:
    """The status in a person's words: the verdict, then the reason behind each half."""
    verdict = (
        t("maintainer: yes (this installation may run the maintainer tools)") if status.ok
        else t("maintainer: no (this installation is not registered as maintainer)")
    )
    lines = [verdict]
    for satisfied, reason in ((status.flag, status.flag_reason), (status.key, status.key_reason)):
        lines.append(f"  {safe_mark('✓') if satisfied else safe_mark('✗')} {reason}")
    lines.append(
        t("The maintainer tools may run on this installation.") if status.ok
        else t("Maintainer tools are refused here: the line above says what is missing.")
    )
    return lines


def cmd_status(args) -> int:
    """Prints the state of both halves, in text or as JSON."""
    status = maintainer.is_maintainer()
    if getattr(args, "json", False):
        print(json.dumps(status.to_dict(), indent=2, ensure_ascii=False))
    else:
        print("\n".join(_lines(status)))
    return 0 if status.ok else 1


def cmd_register(args) -> int:
    """Writes `maintainer: true` into this installation's private config, then checks the key."""
    path = maintainer.config_path()
    if not path.parent.is_dir():
        print(t("There is no private config at {path}.", path=path))
        print(t("Add this line to it, then run this command again:"))
        print(f"  {maintainer.FLAG_FIELD}: true")
        return 1
    try:
        data = read_remotes_file(path)
    except RemotesFileError as exc:
        print(exc, file=sys.stderr)
        return 1
    data["schema_version"] = data.get("schema_version", 1)
    data[maintainer.FLAG_FIELD] = True
    body = (
        "# Written by `nexgen maintainer register`: this installation may run the engine's\n"
        "# maintainer tools. The flag alone authorises nothing; a listed signing key does.\n"
        + yaml.safe_dump(data, sort_keys=False)
    )
    try:
        atomic_write_text(path, body)
    except OSError as exc:
        print(t("Could not write {path}: {error}", path=path, error=exc), file=sys.stderr)
        return 1
    print(t("Registered in {path}.", path=path))
    status = maintainer.is_maintainer()
    print("\n".join(_lines(status)))
    print(t(
        "The private config now has uncommitted changes; publish them with "
        "'nexgen vault push {file}'.",
        file="03-INFRA/agent-universal-layer/sync/remotes.yaml",
    ))
    return 0 if status.ok else 1


def register(sub) -> None:
    """Declares the verbs: listed for a maintainer, still reachable on the machine that needs it.

    A non-maintainer reads `nexgen --help` and sees no maintainer commands, because they cannot
    run there. The verb itself still answers, and the refusal that points at it would be useless
    if it did not: `status` explains what is missing and `register` is how it stops being missing.
    """
    listed = {"help": t("Is this installation registered as maintainer ('status', 'register')")}
    parser = sub.add_parser("maintainer", **(listed if maintainer.tools_available() else {}))
    actions = parser.add_subparsers(dest="maintainer_action", required=True)
    status = actions.add_parser("status", help=t("Say whether this installation may run the maintainer tools"))
    status.add_argument("--json", action="store_true", help=t("Output in JSON format"))
    status.set_defaults(func=cmd_status)
    actions.add_parser(
        "register",
        help=t("Register this installation as maintainer, in its own private config"),
    ).set_defaults(func=cmd_register)

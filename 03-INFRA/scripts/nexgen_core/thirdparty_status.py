"""`nexgen thirdparty status`: what updates by itself, what waits for the owner, and what to type.

One job: turn the census `extensions` already reads into one line per third-party item carrying the
two things the census cannot say on its own — which policy governs that item, and the exact command
(or file) that moves it. Nothing here decides anything: the guardian's verdict is read, never
recomputed, and the answer comes from the manifests plus what the hourly watch last left in the
machine's state folder. Read-only and offline, like `nexgen info`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from nexgen_core import extensions
from nexgen_core.i18n import t
from nexgen_core.marks import safe_text
from nexgen_core.thirdparty_guard import AUTO as TIER_AUTO, BATCH as TIER_BATCH

#: One policy per item, most automatic first. `unchecked` is not a policy but the absence of an
#: answer, and it is named so instead of reading as "up to date".
AUTO = "auto"
YES = "yes"
LOOK = "look"
PINNED = "pinned"
FLOATING = "floating"
BY_HAND = "by-hand"
UNCHECKED = "unchecked"

#: How each policy reads to a person.
_POLICY = {
    AUTO: lambda: t("moves by itself"),
    YES: lambda: t("waits for your yes"),
    LOOK: lambda: t("needs a look first"),
    PINNED: lambda: t("up to date"),
    FLOATING: lambda: t("floating, no pin"),
    BY_HAND: lambda: t("by hand, where it runs"),
    UNCHECKED: lambda: t("never checked"),
}

#: What each policy means for the person reading the line, in one sentence. Only the policies that
#: appear on this machine are printed: an explanation of something absent is noise.
_LEGEND = (
    (AUTO, "the hourly watch raises it by itself, without asking"),
    (YES, "one command for all of them: nexgen skills bump"),
    (LOOK, "no command raises it: read what changed, then edit the file named here"),
    (FLOATING, "nothing here decides what runs: pin a version in the manifest, or let whoever serves it decide"),
    (BY_HAND, "a component of a module: replace it where it runs, after trying it"),
    (UNCHECKED, "no upstream answer yet: the hourly watch does it, or nexgen skills bump now"),
)

#: One command raises everything the guardian cleared, MCP servers and skills alike.
BUMP = "nexgen skills bump"

#: The file a held item is pinned in, per kind.
_MANIFEST = {"mcp": "mcp/manifest.yaml", "skill": "skills.manifest.yaml"}

#: A full commit SHA identifies the commit in its first nine characters, which is what the rest of
#: the engine prints; a version is never shortened.
_SHA = re.compile(r"^[0-9a-f]{32,}$")

_LABEL = {"mcp": lambda: t("MCP"), "skill": lambda: t("skill"), "module": lambda: t("module"),
          "plugin": lambda: t("plugin")}


@dataclass(frozen=True)
class Item:
    """One third-party skill, MCP server or module component, with what governs it."""

    name: str
    kind: str
    pinned: str
    newest: str
    policy: str
    command: str
    note: str


def _short(value: str) -> str:
    """A commit is identified by its first nine characters; a version is never shortened."""
    return value[:9] if _SHA.match(value or "") else (value or "")


def _installer_command(pin: str) -> str:
    """`pkg@1.2.3` -> `npx pkg update`: the vendor's own updater.

    A skill declared `origin: upstream` is installed by its own installer, so the pin in the manifest
    is only what the watch compares: rewriting it would claim a version nobody installed. The command
    is derived from the package the pin names, the way that skill's manifest entry documents it.
    """
    package, _, version = (pin or "").rpartition("@")
    if not package or not version[:1].isdigit():
        return f"npx {pin} update" if pin else ""
    return f"npx {package} update"


def _edit(kind: str) -> str:
    """The manifest that holds this kind's pin, as the thing a person edits."""
    return f"edit {_MANIFEST[kind]}" if kind in _MANIFEST else ""


def policy_and_command(kind: str, origin: str, pin_state: str, update: dict,
                       pin: str = "") -> tuple[str, str]:
    """Which policy governs one item, and the exact thing that moves it.

    `update` is the census' own dict (state, pinned, upstream, verdict, tier, deprecated); `pin` is
    the spec the manifest actually carries, which is what names the package an installer owns. The
    tier is what separates "the guardian cleared it, one yes and it moves" from "somebody has to read
    the change first": both are `stale`, and a command that hides the difference is what made this
    command necessary.
    """
    stale = update.get("state") == "stale"
    tier = update.get("tier")
    if kind == "module":
        # The guardian returns no target for a module's component: it is replaced where it runs.
        if stale:
            return BY_HAND, ""
        return (PINNED, "") if update.get("state") == "current" else (UNCHECKED, BUMP)
    if stale:
        if tier == TIER_AUTO:
            return AUTO, ""
        if tier == TIER_BATCH:
            return YES, BUMP
        if not tier:
            # A newer version with no verdict covering it: the watch ran, the judge has not yet.
            return UNCHECKED, BUMP
        if origin == "upstream":
            return LOOK, _installer_command(pin)
        return LOOK, _edit(kind)
    if update.get("deprecated"):
        # The publisher withdrew this pin. The numbers may even compare "lower" (a date train against
        # semver), so only a person can choose what to pin next.
        return LOOK, _edit(kind)
    if pin_state == "unpinned":
        return FLOATING, _edit(kind)
    if pin_state == "none":
        return FLOATING, ""
    return (PINNED, "") if update.get("state") == "current" else (UNCHECKED, BUMP)


def _item(kind: str, row: dict, pin_state: str, origin: str = "") -> Item:
    """One census row as the line a person reads: what it is, where it stands, what to type."""
    update = row.get("update") or {}
    pin = str(row.get("pin") or "")
    policy, command = policy_and_command(kind, origin, pin_state, update, pin)
    return Item(
        name=str(row.get("name") or ""),
        kind=kind,
        pinned=_short(pin or str(update.get("pinned") or row.get("pinned") or "")),
        newest=_short(str(update.get("upstream") or "")),
        policy=policy,
        command=command,
        note=str(update.get("plain") or update.get("deprecated") or ""),
    )


def build(data: dict) -> list[Item]:
    """One Item per third-party skill, MCP server and module component, from the collected census."""
    items: list[Item] = []
    for row in data.get("mcp") or []:
        if row.get("provenance") == extensions.THIRD_PARTY:
            items.append(_item("mcp", row, str(row.get("pin_state") or "none")))
    for row in data.get("skills") or []:
        if row.get("provenance") == extensions.THIRD_PARTY:
            items.append(_item("skill", row, "pinned" if row.get("pin") else "none",
                               str(row.get("origin") or "")))
    for row in data.get("modules") or []:
        items.append(_item("module", row, "pinned"))
    return sorted(items, key=lambda item: (item.name.lower(), item.kind))


def plugin_items(rows: list) -> list[Item]:
    """One Item per plugin a harness reports; the harness' own command moves it.

    A plugin is never bumped by this engine: the marketplace that installed it
    owns its lifecycle, and re-implementing that update here would mean two
    writers of the same files. The row exists so the machine's own inventory and
    the command are both in the place a person already looks.
    """
    return [
        Item(name=row.name, kind="plugin", pinned=row.version or "-", newest="-",
             policy=BY_HAND, command=row.command,
             note="" if row.enabled else t("{harness}: disabled", harness=row.harness))
        for row in rows
    ]


def _counts(items: list[Item]) -> dict[str, int]:
    """How many items each policy has."""
    return {policy: sum(1 for item in items if item.policy == policy) for policy in _POLICY}


def _summary(items: list[Item], counts: dict[str, int]) -> list[str]:
    """The two numbers that answer the question: how much moves on its own, how much waits."""
    lines = [t("{items} third-party items: {ready} ready to move, {looking} need a look, {handed} updated where they run, {floating} with no pin.",
               items=len(items), ready=counts[AUTO] + counts[YES], looking=counts[LOOK],
               handed=counts[BY_HAND], floating=counts[FLOATING])]
    if counts[YES]:
        lines.append(t("One command moves them: {command}", command=BUMP))
    if counts[AUTO]:
        lines.append(t("{count} of them move by themselves: the hourly watch raises them without asking.",
                       count=counts[AUTO]))
    return lines


def _table(items: list[Item]) -> list[str]:
    """One line per item, widest cell first, so a name never squeezes the command off the row."""
    head = (t("Name"), t("Kind"), t("Pinned"), t("Newest"), t("Policy"), t("What moves it"))
    rows = [(item.name, _LABEL[item.kind](), item.pinned or "-", item.newest or "-",
             _POLICY[item.policy](), item.command or "-") for item in items]
    widths = [max(len(row[column]) for row in [head, *rows]) for column in range(len(head) - 1)]
    lines = ["  " + "  ".join(cell.ljust(width) for cell, width in zip(head, widths)) + "  " + head[-1]]
    for row in rows:
        lines.append("  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths)) + "  " + row[-1])
    return lines


def _legend(items: list[Item]) -> list[str]:
    """Only the policies this machine actually has, each with what to do about it."""
    present = {item.policy for item in items}
    explained = [f"  {_POLICY[policy]().ljust(22)}  {t(text)}"
                 for policy, text in _LEGEND if policy in present]
    # A policy with no entry in the legend (an item that is simply up to date) needs no explanation,
    # and a heading with nothing under it reads as a bug, so no entry means no block.
    return ["", t("How to read this"), *explained] if explained else []


def render(items: list[Item], checked_at: float | None, now: float | None = None) -> str:
    """The whole screen: when upstream was last checked, then the items, then what to do."""
    lines: list[str] = []
    age = extensions.age_text(checked_at, now)
    if age is None:
        lines.append(t("Upstream was never checked on this machine yet: the hourly watch does it, or run {command}.",
                       command=BUMP))
    else:
        lines.append(t("Upstream last checked {age} ago, by the hourly dependency watch.", age=age))
    lines.append("")
    if not items:
        lines.append(t("No third-party skill or MCP server is declared on this machine."))
        return "\n".join(lines)

    counts = _counts(items)
    lines.extend(_summary(items, counts))
    lines.append("")
    lines.extend(_table(items))
    lines.extend(_legend(items))
    notes = [(item.name, item.note) for item in items if item.note]
    if notes:
        lines.append("")
        lines.append(t("Why an item is where it is"))
        for name, note in notes:
            lines.append(f"  {name}: {note}")
    return "\n".join(lines)


def report(*, home: Path | None = None, vault_data: Path | None = None,
           engine_root: Path | None = None, state_dir: Path | None = None,
           now: float | None = None, plugins: bool = True) -> tuple[str, list[Item]]:
    """The report and the rows behind it, from the manifests and the watch's last answer.

    `plugins=False` keeps the command instant and offline: the harnesses are asked
    over their own CLIs, which is a few seconds on a machine that has them.
    """
    data = extensions.collect(home=home, vault_data=vault_data, engine_root=engine_root,
                              state_dir=state_dir)
    items = build(data)
    body = render(items, data.get("upstream_checked_at"), now=now)
    for error in data.get("errors") or []:
        body += "\n" + t("A manifest could not be read: {error}", error=error)
    if plugins:
        from nexgen_core import thirdparty_plugins

        found, problems = thirdparty_plugins.rows()
        body = body + render_plugins(plugin_items(found), problems)
    return body, items


def render_plugins(items: list[Item], problems: list[str]) -> str:
    """The plugins block: their own table, because their updates are not ours.

    Kept apart from the census on purpose. A plugin is not a pin this engine can
    raise, so mixing it into the counts above would answer a question nobody
    asked: the number of things waiting for a yes.
    """
    lines = ["", t("Plugins installed by the harnesses (their own command updates them)")]
    if not items and not problems:
        lines.append("  " + t("No harness on this machine reports a plugin."))
    if items:
        lines.extend(_table(items))
    for problem in problems:
        lines.append("  " + t("{problem}: that harness' plugins are not listed", problem=problem))
    return "\n".join(lines)


def main(*, home: Path | None = None, vault_data: Path | None = None,
         engine_root: Path | None = None, state_dir: Path | None = None,
         now: float | None = None, plugins: bool = True) -> int:
    """Print the report. Read-only: nothing is fetched, applied or written."""
    body, _items = report(home=home, vault_data=vault_data, engine_root=engine_root,
                          state_dir=state_dir, now=now, plugins=plugins)
    print(safe_text(body))
    return 0

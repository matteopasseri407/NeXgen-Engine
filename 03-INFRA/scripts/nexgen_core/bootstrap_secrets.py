"""The secrets absorbed MCP entries reference: asked for, stored, materialized.

Adopting a Claude Desktop server turns its literal credentials into `${NAME}`
references, and from then on the server runs on whatever the vault's encrypted
store holds for that name. Nothing fills those values in by itself, so the first
install has to ask: this module does the asking, writes every value with
`--create-only` (a concurrent writer loses safely instead of last-write-wins),
and materializes the store once at the end.

It lives beside `bootstrap.py` rather than inside it because the installer's own
job is prerequisites, structure and the three questions; the guided wiring is a
separate conversation that only some installs ever have. The terminal helpers are
here because this is where they are used: the install's own questions reuse
`_ask`.
"""
from __future__ import annotations

import getpass
import subprocess
import sys
from pathlib import Path

from nexgen_core.i18n import t


def _print_manual_fix(masked: list[str]) -> None:
    """Servers whose secrets were cut out with no reference to wire: the
    only signal before a runtime 401. Printed on every exit, including
    the early ones, or the fresh-host case goes silent exactly when it
    matters most."""
    if masked:
        print("  " + t("manual fix needed (secrets cut out, no reference to wire): {names}.",
                        names=", ".join(masked)))


def _ask(prompt: str, options: str) -> str:
    try:
        return input(f"  {prompt} [{options}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):  # nexgen: allow reason="nobody at the terminal means an empty answer"
        return ""


def _read_secret_value(prompt: str) -> str | None:
    """One secret from the terminal: no echo, no case folding, no trimming.

    Deliberately not `_ask`: lowercasing would destroy case-sensitive
    tokens, and echo would leak them into scrollback. EOF or an empty
    value means "skip this name", never "store an empty secret".
    """
    try:
        value = getpass.getpass(f"  {prompt}: ")
    except (EOFError, KeyboardInterrupt):  # nexgen: allow reason="EOF or Ctrl-C skips this secret"
        return None
    return value or None


def _store_script(root: Path) -> Path:
    return root / "03-INFRA" / "scripts" / "nexgen-secrets.py"


def _store_names(script: Path) -> tuple[bool, set[str], set[str], int]:
    """Trial decrypt plus inventory: (ok, shared, host, recipients).

    The human-readable `list` output is the contract: "shared:",
    "per_host (...):", two-space names. The store prints a single per_host
    section for the current host only (`per_host.get(HOSTNAME)`), so every
    parsed host name belongs here; a future multi-host listing would only
    ever skip a question, and the doctor WARN would still name the secret.
    A zero exit with unparseable output yields empty sets; every write goes
    through --create-only, so a concurrent writer still loses safely
    instead of last-write-wins.
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "list"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):  # nexgen: allow reason="an unreadable store is reported, not raised"
        return False, set(), set(), 0
    if proc.returncode != 0:
        return False, set(), set(), 0
    shared, host, section, recipients = set(), set(), None, 0
    for line in proc.stdout.splitlines():
        if line.startswith("shared:"):
            section = shared
        elif line.startswith("per_host"):
            section = host
        elif line.startswith("recipients:"):
            section = None
            try:
                recipients = int(line.split(":")[1].split()[0])
            except (IndexError, ValueError):
                recipients = 0
        elif section is not None and line.startswith("  ") and line.strip():
            section.add(line.strip().split(":")[0])
    return True, shared, host, recipients


def _store_set(script: Path, name: str, value: str, *, host_only: bool) -> bool:
    """Create-only write through the store's own commands (stdin, synced).

    `--create-only` makes the store refuse an existing name instead of
    overwriting it: the names snapshot above decides, and a concurrent
    writer loses safely instead of last-write-wins.
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "set-host" if host_only else "set",
             "--create-only", name],
            input=value.encode("utf-8"), capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):  # nexgen: allow reason="a failed write is reported per name"
        return False
    return proc.returncode == 0


def _store_materialize(script: Path) -> bool:
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "materialize"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):  # nexgen: allow reason="the caller reports the failure"
        return False
    return proc.returncode == 0


def _materialize_best_effort(script: Path) -> bool:
    """Materialize that never raises into the install flow: a failure is a
    message, not a crash, and the caller reports it."""
    try:
        return _store_materialize(script)
    except Exception:  # nexgen: allow reason="install step reports, never raises"
        return False


def _wire_absorbed_secrets_impl(root: Path, refs: dict[str, list[str]], masked: list[str] | None = None) -> None:
    """Guided wiring for the ${NAME}s absorbed entries reference.

    Creates only: names already in the store are materialized, never
    rewritten, and overwriting stays an explicit `set` outside init.
    Skipped, externally-managed, and manually-to-fix names end up in the
    closing summary, so no server silently runs on an empty value.
    """
    masked = sorted(masked or [])
    script = _store_script(root)
    if not script.is_file():
        _print_manual_fix(masked)
        print("  " + t("no secrets store in this vault: wire the values by hand."))
        return
    ok, shared, host, recipients = _store_names(script)
    if not ok:
        _print_manual_fix(masked)
        if refs:
            print("  " + t("this host cannot open the secrets store yet (enroll first):"))
            for name in sorted(refs):
                print(f"    - {name}")
            print("  " + t("after enrollment, wire them with 'nexgen-secrets set <name>' (shared) "
                            "or 'nexgen-secrets set-host <name>' (this host only)."))
        else:
            print("  " + t("this host cannot open the secrets store yet (enroll first)."))
        return
    missing = [n for n in sorted(refs) if n not in shared and n not in host]
    if not missing:
        if refs:
            if _materialize_best_effort(script):
                print("  " + t("every referenced secret is already stored: materialized."))
            else:
                print("  " + t("every referenced secret is already stored, but materialize failed: "
                                "run 'nexgen-secrets materialize' by hand."))
        _print_manual_fix(masked)
        return
    wired, skipped, external = [], [], []
    for name in missing:
        servers = refs[name]
        if len(servers) > 1:
            print("  " + t("warning: {name} is referenced by {count} servers ({servers}): "
                            "one value will serve all of them.",
                            name=name, count=len(servers), servers=", ".join(servers)))
            if not _ask(t("use one value anyway?"), "y / N").startswith("y"):
                skipped.append(name)
                continue
        choice = _ask(
            t("{name}: save for this host, all hosts, externally managed, or skip?", name=name),
            t("H=host (default) / A=all {hosts} / E=external / S=skip",
              hosts=f"{recipients} enrolled hosts" if recipients > 0 else "enrolled hosts"),
        )
        if choice.startswith("e"):
            external.append(name)
            continue
        if choice.startswith("s"):
            skipped.append(name)
            continue
        value = None
        if choice.startswith("a") or choice.startswith("h") or choice == "":
            value = _read_secret_value(t("value for {name} (hidden, empty skips)", name=name))
        if value is None:
            skipped.append(name)
            continue
        if _store_set(script, name, value, host_only=not choice.startswith("a")):
            wired.append(name)
        else:
            print("  " + t("could not store {name}: skipped.", name=name))
            skipped.append(name)
    materialized = _materialize_best_effort(script)
    print("  " + t("wired: {names}.", names=", ".join(wired) if wired else t("none")))
    if not materialized:
        print("  " + t("materialize failed: run 'nexgen-secrets materialize' by hand "
                        "before the values reach the environment."))
    if skipped:
        print("  " + t("inactive until wired: {names}.", names=", ".join(skipped)))
    if external:
        print("  " + t("externally managed (not checked): {names}.", names=", ".join(external)))
    _print_manual_fix(masked)


def wire_absorbed_secrets(root: Path, refs: dict[str, list[str]], masked: list[str] | None = None) -> None:
    """Guided wiring for the ${NAME}s absorbed entries reference."""
    return _wire_absorbed_secrets_impl(root, refs, masked)

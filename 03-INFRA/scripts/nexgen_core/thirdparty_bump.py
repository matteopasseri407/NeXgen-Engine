"""One-yes batch apply for guardian-vetted third-party pins.

The guardian judges; this module moves. It only touches pins the guardian
already marked AUTO (provably identical bytes) or BATCH (safe-shaped and
small), and only after one explicit human yes listing every pin with its
plain-language reason. HOLD items are never rewritten by this path.

Pin edits are surgical string replacements of the exact old pin (a full
commit SHA or a `pkg@version` token), so manifest comments and layout
survive byte for byte. Both manifests are backed up next to themselves
before any write, revalidated after, and only then materialized.
"""
from __future__ import annotations

import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.i18n import t  # noqa: E402
from nexgen_core.paths import (  # noqa: E402
    mcp_manifest,
    resolve_engine_root,
    resolve_home,
    resolve_state_dir,
    resolve_vault_data,
    skills_manifest,
)

GUARD_FILE_NAME = "third-party-guard.json"
GUARD_FRESH_HOURS = 24
APPLIED_FILE_NAME = "third-party-applied.json"
APPLIED_KEEP_DAYS = 30


def _guard_file(state_dir: Path) -> Path:
    return Path(state_dir) / "nexgen" / GUARD_FILE_NAME


def _read_guard(state_dir: Path) -> dict | None:
    import json

    try:
        data = json.loads(_guard_file(state_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _guard_fresh(payload: dict | None) -> bool:
    if not payload:
        return False
    try:
        age_hours = (time.time() - float(payload.get("checked_at", 0))) / 3600
    except (TypeError, ValueError):
        return False
    return age_hours < GUARD_FRESH_HOURS


def read_guard_payload(state_dir: Path) -> dict | None:
    """The verdict file the hourly beat leaves behind, if any."""
    return _read_guard(Path(state_dir))


def refresh_verdicts(home: Path, vault_data: Path, state_dir: Path) -> dict | None:
    """Recomputes depwatch findings plus guardian verdicts, synchronously.

    Only used when the hourly beat hasn't left fresh verdicts: the human
    invoked this command, so waiting on bounded network checks is their
    choice, not a background surprise.
    """
    from nexgen_core.beat import Heartbeat

    beat = Heartbeat(state_dir=state_dir, vault_data=vault_data,
                     engine_root=resolve_engine_root(home))
    watch = beat.run_dependency_watch()
    if not watch.get("ok"):
        return None
    return _read_guard(state_dir)


def collect_plan(payload: dict) -> tuple[list[dict], list[dict]]:
    """Splits verdicts into raisable pins and held ones.

    A raisable item needs a precise target; a verdict without one stays
    held no matter what the judge said. Tiers stay separate: the hourly
    beat moves AUTO on its own, a human says one yes for BATCH.
    """
    auto, batch, held = [], [], []
    for tier, bucket in (("auto", auto), ("batch", batch)):
        items = payload.get(tier)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            (bucket if isinstance(item.get("target"), dict) else held).append(item)
    hold_items = payload.get("hold")
    if isinstance(hold_items, list):
        for item in hold_items:
            if isinstance(item, dict):
                held.append(item)
    return auto, batch, held


def _target_of(item: object) -> dict:
    """The bump target of a verdict item, or {} when malformed.

    A non-dict target (or a non-dict item) never crashes the command:
    without a precise target the item is simply not raisable.
    """
    if not isinstance(item, dict):
        return {}
    target = item.get("target")
    return target if isinstance(target, dict) else {}


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    n = 2
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak-{stamp}-{n}")
        n += 1
    try:
        shutil.copy2(path, backup)
        return backup
    except OSError:
        return None


def _restore(path: Path, backup: Path) -> bool:
    """Restores path from backup without ever truncating it.

    Goes through a temp file plus rename like every other write here:
    with a full disk a plain copy could truncate the manifest mid-restore
    and destroy the only good copy left.
    """
    try:
        content = backup.read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        _atomic_write(path, content)
        return True
    except OSError:
        return False


def _entry_span(text: str, name: str) -> tuple[int, int] | None:
    """(start, end) offsets of the `  name:` block, or None.

    Manifest entries are two-space-indented maps with deeper-indented
    bodies; the block runs until a blank line, a less-indented line, or
    the next entry. Anything outside the approved carriers' blocks
    (comments, held entries) is never rewritten.
    """
    import re

    match = re.search(rf"^  {re.escape(name)}:\s*\n", text, re.MULTILINE)
    if not match:
        return None
    end = match.end()
    for line in text[end:].splitlines(keepends=True):
        if line.strip() == "" or line.startswith("    ") or re.match(r"^  #", line):
            end += len(line)
        else:
            break
    return match.start(), end


def _replace_in_entries(text: str, carriers: list[tuple[str, str | None]],
                        old: str, new: str) -> tuple[str, int]:
    """Rewrites old to new on exactly the approved carriers' pin lines.

    Git pins match only their own field line (`commit:` or `rev:`), so a
    held field with the same SHA in the same entry stays put. Npm tokens
    rewrite inside the entry block, where install token and deps spec
    name the same package upgrade.
    """
    import re

    def _field_pattern(field: str | None) -> re.Pattern[str] | None:
        if field == "commit":
            return re.compile(rf"^(\s*commit:\s*[\"']?){re.escape(old)}([\"']?\s*(?:#.*)?)$",
                              re.MULTILINE)
        if field == "deps.rev":
            return re.compile(rf"^(\s*rev:\s*[\"']?){re.escape(old)}([\"']?\s*(?:#.*)?)$",
                              re.MULTILINE)
        return None

    replaced = 0
    for name, field in carriers:
        span = _entry_span(text, name)
        if span is None:
            continue
        start, end = span
        block = text[start:end]
        pattern = _field_pattern(field)
        if pattern is None:
            # Token-boundary replace: approving pkg@1.0.0 must never
            # rewrite otherpkg@1.0.0 in the same entry.
            token_pat = re.compile(rf"(?<![\w@./-]){re.escape(old)}(?![\w@./+-])")
            block, count = token_pat.subn(new, block)
            if not count:
                continue
        else:
            block, count = pattern.subn(rf"\g<1>{new}\g<2>", block)
            if not count:
                continue
        replaced += 1
        text = text[:start] + block + text[end:]
    return text, replaced


def _atomic_write(path: Path, content: str) -> None:
    """Writes through a temp file plus rename: a failure mid-write can
    never leave a truncated manifest behind."""
    import os

    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def apply_plan(
    raisable: list[dict], vault_data: Path, *, sync: bool = True,
    home: Path | None = None,
) -> tuple[int, list[str]]:
    """Rewrites the pins atomically, revalidates, and materializes.

    Callers hold the bump lock around this call (see bump_batch and
    auto_apply): reads, planning and writes stay one critical section,
    so two parallel bumps can never compute on the same text and have
    the second silently undo the first.
    """
    skills_path = skills_manifest(vault_data)
    mcp_path = mcp_manifest(vault_data)
    try:
        skills_text = skills_path.read_text(encoding="utf-8")
    except OSError as exc:
        return 0, [f"[ERROR] cannot read {skills_path}: {exc}"]
    try:
        mcp_text = mcp_path.read_text(encoding="utf-8") if mcp_path.is_file() else ""
    except OSError as exc:
        return 0, [f"[ERROR] cannot read {mcp_path}: {exc}"]

    try:
        from nexgen_core.config import load_mcp_manifest, load_skills_manifest

        skills_raw = load_skills_manifest(skills_path).get("skills", {})
        mcp_raw = load_mcp_manifest(mcp_path).get("servers", {}) if mcp_path.is_file() else {}
    except Exception:
        skills_raw, mcp_raw = {}, {}

    def _approved(change: tuple, name: str, field: str | None) -> bool:
        """This carrier approves exactly this change (kind, old, new).

        Git pins approve per field: a commit approval never covers a
        deps.rev with the same SHA. Npm approvals cover the whole entry:
        install token and deps spec name the same package upgrade.
        """
        kind, old, new = change
        for cand in raisable:
            target = cand.get("target") or {}
            if (target.get("kind") != kind
                    or str(cand.get("pinned") or "") != old
                    or str(cand.get("upstream") or "") != new
                    or (target.get("skill") != name and target.get("server") != name)):
                continue
            if field in ("commit", "deps.rev") and target.get("field") != field:
                continue
            return True
        return False

    def _skill_carriers_of_sha(sha: str) -> list[tuple[str, str]]:
        """(entry, field) pairs pinned to this SHA, by commit or git dep."""
        carriers = []
        for n, e in skills_raw.items():
            if not isinstance(e, dict):
                continue
            if e.get("origin") == "github" and e.get("commit") == sha:
                carriers.append((n, "commit"))
            deps = e.get("deps") or {}
            if (isinstance(deps, dict) and deps.get("kind") == "git"
                    and str(deps.get("rev") or "") == sha):
                carriers.append((n, "deps.rev"))
        return carriers

    def _skill_carriers_of_token(token: str) -> list[str]:
        carriers = []
        for n, e in skills_raw.items():
            if not isinstance(e, dict):
                continue
            install = e.get("install") or []
            tokens = [str(x) for x in install] if isinstance(install, list) else []
            deps = e.get("deps") or {}
            spec = str(deps.get("spec") or "") if isinstance(deps, dict) else ""
            if token in tokens or token == spec:
                carriers.append(n)
        return carriers

    def _mcp_carriers_of_token(token: str) -> list[str]:
        from nexgen_core.depwatch import _command_tokens  # noqa: E402

        carriers = []
        for n, srv in mcp_raw.items():
            if isinstance(srv, dict) and token in _command_tokens(srv):
                carriers.append(n)
        return carriers

    notes: list[str] = []
    planned: dict[tuple, dict] = {}

    def _schedule(item: dict, where: str, old: str, new: str,
                  carriers: list, field: str | None) -> bool:
        # Approval compares versions (what the verdicts judged); old/new
        # are replacement strings (SHAs or pkg@version tokens).
        vold, vnew = str(item.get("pinned") or ""), str(item.get("upstream") or "")
        kind = str((_target_of(item)).get("kind") or "")
        blocked = [n for (n, f) in carriers if not _approved((kind, vold, vnew), n, f)]
        if blocked:
            other = blocked[0]
            notes.append(t("'{what}': held back, '{other}' shares the pin and is not approved",
                           what=item.get("what"), other=other))
            return False
        key = (str((_target_of(item)).get("kind") or ""), where, old, new, field)
        group = planned.setdefault(key, {"items": [], "carriers": []})
        group["items"].append(item)
        for carrier in carriers:
            if carrier not in group["carriers"]:
                group["carriers"].append(carrier)
        return True

    for item in raisable:
        target = _target_of(item)
        old, new = str(item.get("pinned") or ""), str(item.get("upstream") or "")
        if not old or not new or old == new:
            continue
        kind = str(target.get("kind") or "")
        if kind == "git-commit":
            # One field per change: a commit approval never touches a
            # deps.rev with the same SHA, even in the same entry.
            field = str(target.get("field") or "commit")
            carriers = [(n, f) for (n, f) in _skill_carriers_of_sha(old) if f == field]
            if not carriers:
                notes.append(t("'{what}': pin already moved, skipped", what=item.get("what")))
                continue
            _schedule(item, "skills", old, new, carriers, field)
        elif kind == "npm-version":
            package = str(target.get("package") or "")
            token_old, token_new = f"{package}@{old}", f"{package}@{new}"
            if target.get("manifest") == "mcp":
                carriers = [(n, None) for n in _mcp_carriers_of_token(token_old)]
                if not carriers:
                    notes.append(t("'{what}': pin already moved, skipped", what=item.get("what")))
                    continue
                _schedule(item, "mcp", token_old, token_new, carriers, None)
            else:
                entry = skills_raw.get(str(target.get("skill") or "")) or {}
                if entry.get("origin") == "upstream":
                    # The bytes come from the vendor's own installer, never
                    # from this pin: rewriting the spec alone would claim a
                    # version that isn't installed. Held for the installer.
                    notes.append(t("'{what}': upstream-owned, update it with its installer",
                                   what=item.get("what")))
                    continue
                carriers = [(n, None) for n in _skill_carriers_of_token(token_old)]
                if not carriers:
                    notes.append(t("'{what}': pin already moved, skipped", what=item.get("what")))
                    continue
                _schedule(item, "skills", token_old, token_new, carriers, None)
        else:
            notes.append(t("'{what}': no precise target, skipped", what=item.get("what")))

    if not planned:
        return 0, notes, []

    # Field-scoped replacements: only the approved carriers' own pin lines
    # change. A comment mentioning the same string, or a held field with
    # the same SHA, can never be dragged along.
    new_skills, new_mcp = skills_text, mcp_text
    for (kind, where, old, new, field), group in planned.items():
        if where == "skills":
            new_skills, replaced = _replace_in_entries(new_skills, group["carriers"], old, new)
        else:
            new_mcp, replaced = _replace_in_entries(new_mcp, group["carriers"], old, new)
        group["replaced"] = replaced
        if not replaced:
            for item in group["items"]:
                notes.append(t("'{what}': pin already moved, skipped", what=item.get("what")))

    def _write_phase() -> tuple[int, list[str], dict[str, Path]]:
        backups: dict[str, Path] = {}
        if new_skills != skills_text:
            backup = _backup(skills_path)
            if backup is None:
                return 0, ["[ERROR] " + t("cannot back up {path}, nothing written", path=skills_path)], {}
            backups["skills"] = backup
        if new_mcp != mcp_text and mcp_path.is_file():
            backup = _backup(mcp_path)
            if backup is None:
                return 0, ["[ERROR] " + t("cannot back up {path}, nothing written", path=mcp_path)], {}
            backups["mcp"] = backup
        try:
            # Atomic writes: temp file plus rename, so a failure (full
            # disk included) leaves the original intact instead of truncated.
            # If the second write fails, the first is restored: half a plan
            # is never left applied.
            if "skills" in backups:
                _atomic_write(skills_path, new_skills)
            if "mcp" in backups:
                try:
                    _atomic_write(mcp_path, new_mcp)
                except OSError as exc:
                    restore_notes = []
                    if "skills" in backups and not _restore(skills_path, backups["skills"]):
                        restore_notes.append(t("could not restore {path} from {backup}",
                                               path=skills_path, backup=backups["skills"]))
                    return 0, (["[ERROR] " + t("write failed ({error}), manifests restored from backups",
                                                error=exc)]
                               + [f"[ERROR] {note}" for note in restore_notes]), {}
        except OSError as exc:
            return 0, ["[ERROR] " + t("write failed ({error}), manifests untouched",
                                       error=exc)], {}
        problems = _revalidate(vault_data, home)
        if problems:
            restore_failures = []
            for key, backup in backups.items():
                if not _restore(skills_path if key == "skills" else mcp_path, backup):
                    restore_failures.append(t("could not restore {path} from {backup}",
                                              path=skills_path if key == "skills" else mcp_path,
                                              backup=backup))
            restored = t("edits rolled back, manifests restored from {backups}",
                         backups=", ".join(str(b) for b in backups.values()))
            return 0, ["[ERROR] " + restored, *problems,
                       *(f"[ERROR] {note}" for note in restore_failures)], {}
        return 1, [t("Backups kept next to the manifests: {backups}",
                     backups=", ".join(str(b) for b in backups.values()))], backups

    phase_ok, phase_notes, phase_backups = _write_phase()
    notes.extend(phase_notes)
    if not phase_ok:
        return 0, notes, []

    # Pins are written but the bytes aren't installed yet. If the
    # materialization fails, the pins go back too: a manifest claiming a
    # version nobody installed would never retry the install.

    def _rollback_pins() -> list[str]:
        """Restores both manifests from backup. A failed restore is
        reported loudly: claiming a rollback that did not happen would
        be worse than the failure itself."""
        failures = []
        for key in ("skills", "mcp"):
            backup = phase_backups.get(key)
            if backup is not None and not _restore(
                    skills_path if key == "skills" else mcp_path, backup):
                failures.append(t("could not restore {path} from {backup}",
                                  path=skills_path if key == "skills" else mcp_path,
                                  backup=backup))
        return failures

    bumps = 0
    moved: list[dict] = []
    for (kind, where, old, new, _field), group in planned.items():
        if not group.get("replaced"):
            continue
        for item in group["items"]:
            bumps += 1
            moved.append(item)
            if kind == "git-commit":
                notes.append(t("'{what}': pin {old} -> {new}",
                               what=item.get("what"), old=old[:9], new=new[:9]))
            else:
                notes.append(t("'{what}': {old} -> {new}",
                               what=item.get("what"), old=old, new=new))

    if sync:
        try:
            sync_notes = _rematerialize(vault_data, home,
                                        any(w == "skills" for _, w, _, _, _ in planned),
                                        any(w == "mcp" for _, w, _, _, _ in planned))
        except Exception as exc:
            sync_notes = [f"[ERROR] materialization failed ({exc})"]
        failed = [n for n in sync_notes if n.startswith("[ERROR]")]
        notes.extend(sync_notes)
        if failed:
            restore_failures = _rollback_pins()
            rolled = t("install failed, pins rolled back to retry next round")
            return 0, [f"[ERROR] {note}" if note.startswith("[ERROR]") else note
                       for note in failed] + [rolled] + [
                f"[ERROR] {note}" for note in restore_failures], []
    return bumps, notes, moved


def _revalidate(vault_data: Path, home: Path | None) -> list[str]:
    from nexgen_core.config import ConfigError, load_mcp_manifest, load_skills_manifest
    from nexgen_core.skills import SkillMaterializer

    problems: list[str] = []
    try:
        load_skills_manifest(skills_manifest(vault_data))
    except ConfigError as exc:
        problems.append(str(exc))
    try:
        load_mcp_manifest(mcp_manifest(vault_data))
    except ConfigError as exc:
        problems.append(str(exc))
    if not problems:
        mat = SkillMaterializer(vault_data=vault_data, home=home)
        problems.extend(mat.validate_manifest())
    return problems


def _rematerialize(vault_data: Path, home: Path | None, skills: bool, mcp: bool) -> list[str]:
    from nexgen_core.paths import resolve_engine_root as _engine_root

    notes: list[str] = []
    engine_root = _engine_root(home)
    if skills:
        from nexgen_core.skills import SkillMaterializer

        mat = SkillMaterializer(vault_data=vault_data, engine_root=engine_root, home=home)
        _, actions = mat.materialize(apply=True)
        notes.extend(actions)
    if mcp:
        from nexgen_core.renderer import McpRenderer

        rend = McpRenderer(vault_data=vault_data, engine_root=engine_root, home=home)
        rend.render_all(write=True)
        notes.append(t("MCP configurations regenerated for every CLI"))
    return notes


def bump_batch(
    *,
    home: Path | None = None,
    vault_data: Path | None = None,
    state_dir: Path | None = None,
    input_fn: Callable[[str], str] = input,
    sync: bool = True,
) -> int:
    """The one-yes command: shows every BATCH pin in plain words, moves
    them on a single yes, leaves HOLD items untouched. AUTO pins already
    moved on their own in the hourly beat and never appear here."""
    resolved_home = resolve_home(home)
    resolved_vault = resolve_vault_data(resolved_home, override=vault_data)
    resolved_state = resolve_state_dir(resolved_home, override=state_dir)

    payload = _read_guard(resolved_state)
    if not _guard_fresh(payload):
        print(t("Verdicts are stale or missing, recomputing them now (bounded network checks)..."))
        payload = refresh_verdicts(resolved_home, resolved_vault, resolved_state)
    if not payload:
        print(t("No verdicts available right now (offline?). Nothing moved."), file=sys.stderr)
        return 1

    auto, batch, held = collect_plan(payload)
    if auto:
        print(t("{count} identical-bytes pins already moved on their own:",
                count=len(auto)))
        for item in auto:
            print(f"  - {item.get('what')}: {item.get('plain') or ''}")
    raisable = batch
    if not raisable:
        print(t("Nothing vetted to raise. {held} items stay held.", held=len(held)))
        for item in held:
            print(f"  - {item.get('what')}: {item.get('plain') or ''}")
        return 0

    print(t("These {count} updates passed the deterministic checks:", count=len(raisable)))
    for item in raisable:
        print(f"  - {item.get('what')}: {item.get('plain') or ''}")
        print(f"    {item.get('pinned')} -> {item.get('upstream')}")
    if held:
        print(t("{held} items stay held (never touched by this command):", held=len(held)))
        for item in held:
            print(f"  - {item.get('what')}: {item.get('plain') or ''}")

    try:
        answer = input_fn(t("Raise these {count} pins? [s/N] ", count=len(raisable)))
    except EOFError:
        return 0
    if answer.strip().lower() not in {"s", "si", "y", "yes"}:
        print(t("Nothing moved."))
        return 0

    from nexgen_core.lock import EXIT_BUSY_MANUAL, HostLock, LockTimeoutError  # noqa: E402

    try:
        with HostLock(lock_path=resolved_state / "third-party-bump.lock",
                      timeout=30, command_name="third-party-bump"):
            bumps, notes, _moved = apply_plan(raisable, resolved_vault, sync=sync,
                                          home=resolved_home)
    except LockTimeoutError:
        print(t("Another bump is already running, retry in a minute."))
        return EXIT_BUSY_MANUAL
    failed = [n for n in notes if n.startswith("[ERROR]")]
    for note in notes:
        print(("  ✗ " if note.startswith("[ERROR]") else "  ✓ ") + note)
    return 1 if failed else 0


def _short_name(what: str) -> str:
    import re

    match = re.match(r"^(?:skill|MCP server) '([^']+)'", str(what or ""))
    return match.group(1) if match else str(what or "")


def _commit_manifests(vault_data: Path, raisable: list[dict]) -> bool:
    """Commits exactly the two manifests, never pushes.

    The pin bump is mechanical and traceable; the message names the pins
    so it reads as one line in history. A failure here is harmless: the
    next guard cycle sweeps dirty infra files anyway.
    """
    from nexgen_core.git_ops import run_git as _run

    names = sorted({_short_name(item.get("what")) for item in raisable if item.get("what")})
    label = ", ".join(names)[:100]
    paths = [p for p in (skills_manifest(vault_data), mcp_manifest(vault_data)) if p.is_file()]
    try:
        # If the human already staged their own edits on these files, hands
        # off: the next guard cycle sweeps everything with its own message.
        staged = _run(vault_data, "diff", "--cached", "--name-only", "--",
                      *[str(p) for p in paths])
        if staged.returncode == 0 and staged.stdout.strip():
            return False
        # Pathspec-only commit: staged files the human prepared elsewhere
        # are never swept into this mechanical commit.
        if _run(vault_data, "add", "--", *[str(p) for p in paths]).returncode != 0:
            return False
        result = _run(vault_data, "commit", "-m", f"chore(pins): guardian auto-bump {label}",
                      "--", *[str(p) for p in paths])
        return result.returncode == 0
    except Exception:
        return False


def _record_applied(state_dir: Path, raisable: list[dict]) -> None:
    """Remembers what moved, so the shell lane can say it once and stop."""
    import json

    sidecar = Path(state_dir) / "nexgen" / APPLIED_FILE_NAME
    try:
        current = json.loads(sidecar.read_text(encoding="utf-8"))
        entries = current.get("applied") if isinstance(current, dict) else None
        entries = list(entries) if isinstance(entries, list) else []
    except (OSError, ValueError):
        entries = []
    seen = {(str(e.get("what")), str(e.get("new"))) for e in entries if isinstance(e, dict)}
    now = time.time()
    for item in raisable:
        marker = (str(item.get("what")), str(item.get("upstream")))
        if marker in seen:
            continue
        seen.add(marker)
        entries.append({"what": item.get("what"), "new": item.get("upstream"), "at": now})
    cutoff = now - APPLIED_KEEP_DAYS * 86400

    def _at(entry: object) -> float:
        try:
            return float(entry.get("at", 0))  # type: ignore[union-attr]
        except (TypeError, ValueError):
            return 0.0

    entries = [e for e in entries if isinstance(e, dict) and _at(e) > cutoff]
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({"applied": entries}, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        pass


def auto_apply(
    payload: dict,
    vault_data: Path | None,
    home: Path | None,
    state_dir: Path | None,
    sync: bool = True,
) -> dict:
    """Silent path for the hourly beat: provably-identical pins move alone.

    Only AUTO verdicts (vendored bytes identical, zero behavior change)
    move here; BATCH still wants its one human yes, HOLD is never touched.
    Never raises, never notifies, never pushes.
    """
    try:
        auto, _, _ = collect_plan(payload)
        if not auto:
            return {"ok": True, "applied": 0}
        resolved_home = resolve_home(home)
        resolved_vault = resolve_vault_data(resolved_home, override=vault_data)
        resolved_state = resolve_state_dir(resolved_home, override=state_dir)
        from nexgen_core.lock import HostLock, LockTimeoutError  # noqa: E402

        try:
            with HostLock(lock_path=resolved_state / "third-party-bump.lock",
                          timeout=30, command_name="third-party-bump"):
                bumps, notes, moved = apply_plan(auto, resolved_vault, sync=sync,
                                                 home=resolved_home)
                errors = [n for n in notes if n.startswith("[ERROR]")]
                if moved:
                    _commit_manifests(resolved_vault, moved)
                    _record_applied(resolved_state, moved)
        except LockTimeoutError:
            return {"ok": True, "applied": 0, "busy": True}
        return {"ok": not errors, "applied": bumps,
                "error": "; ".join(errors) if errors else None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

"""update-notifier state + engine cache. No UI, no boot. Owned here, re-exported by update_notifier for compat."""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from nexgen_core.files import atomic_write_text
from nexgen_core.paths import resolve_home, resolve_state_dir

THROTTLE_HOURS = 12
CACHE_STALE_AFTER_HOURS = 12
CACHE_TOO_OLD_TO_NAG_DAYS = 7

#: Machine-readable twin depwatch writes next to its markdown report.
#: The notifier lanes read this file only and never touch the network
#: for third-party state: freshness comes from the hourly beat and the
#: detached background refresh, same service as the engine cache.
SKILLS_REPORT_NAME = "third-party-upgrades.md"

def _state_file() -> Path:
    return Path(resolve_state_dir()) / "nexgen" / "update-status.json"


def _today() -> str:
    return datetime.date.today().isoformat()


def _read_state() -> dict:
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(patch: dict) -> None:
    from nexgen_core.files import atomic_write_text

    try:
        path = _state_file()
        data = _read_state()
        data.update(patch)
        atomic_write_text(path, json.dumps(data, indent=2) + "\n")
    except OSError:
        pass


def _is_throttled() -> bool:
    # Legacy throttle file (pre-2.3.0 GUI lane): honored so an old record
    # still suppresses a same-day repeat after the upgrade.
    legacy = resolve_home() / ".config" / "nexgen" / "last_update_check.json"
    try:
        if legacy.is_file():
            data = json.loads(legacy.read_text(encoding="utf-8"))
            if (time.time() - data.get("timestamp", 0)) < THROTTLE_HOURS * 3600:
                return True
    except (OSError, ValueError):
        pass
    return False


def _record_prompt_time(latest: str) -> None:
    try:
        legacy = resolve_home() / ".config" / "nexgen" / "last_update_check.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(legacy, json.dumps({"timestamp": time.time(), "latest": latest}, indent=2) + "\n")
    except OSError:
        pass
    _write_state({"dismissed": {"version": latest, "day": _today()}})


def _dismissed_today(latest: str) -> bool:
    dismissed = _read_state().get("dismissed")
    return (
        isinstance(dismissed, dict)
        and dismissed.get("version") == latest
        and dismissed.get("day") == _today()
    )


def _skills_report_file() -> Path:
    return Path(resolve_state_dir()) / SKILLS_REPORT_NAME


def _skills_dismissed(key: str, fingerprint: str) -> bool:
    dismissed = _read_state().get(key)
    return (
        isinstance(dismissed, dict)
        and dismissed.get("fingerprint") == fingerprint
        and dismissed.get("day") == _today()
    )


def _record_skills_dismissal(key: str, fingerprint: str) -> None:
    _write_state({key: {"fingerprint": fingerprint, "day": _today()}})


def _newest_tag_ls_remote(engine_repo: str, timeout: int = 20) -> str | None:
    """Newest released semver tag on origin, without touching the checkout.

    Read-only (`ls-remote`): no fetch, no ref moves, safe to run from the
    guard cycle and from background refresh alike. None on any failure
    (offline, no remote, no tags): callers treat that as "unknown", never
    as an error worth surfacing.
    """
    return _resolve_newest_tag(engine_repo, timeout=timeout, _local=True)


def _resolve_newest_tag(engine_repo: str, timeout: int = 20, _local: bool = False) -> str | None:
    """Facade-aware tag lookup: tests patch update_notifier._newest_tag_ls_remote."""
    if not _local:
        try:
            from . import update_notifier as _fac

            func = getattr(_fac, "_newest_tag_ls_remote", None)
            if func is not None and func is not _newest_tag_ls_remote:
                return func(engine_repo, timeout=timeout)
        except ImportError:
            pass
    import re

    try:
        proc = subprocess.run(
            ["git", "-C", engine_repo, "ls-remote", "--tags", "--refs", "origin"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    semver = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
    best: tuple[int, int, int] | None = None
    best_tag: str | None = None
    for line in proc.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or not fields[1].startswith("refs/tags/"):
            continue
        tag = fields[1].removeprefix("refs/tags/")
        match = semver.fullmatch(tag)
        if not match:
            continue
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if best is None or version > best:
            best, best_tag = version, tag
    return best_tag


def refresh_update_cache(engine_repo: str | None = None, timeout: int = 20) -> dict:
    """Refreshes the shell-hook cache. Never raises, never prints: it runs
    from the guard cycle and from detached background refreshes, where any
    noise would land in a log nobody reads or a shell that just started."""
    from nexgen_core.paths import resolve_engine_root

    result: dict = {"checked_at": time.time()}
    try:
        repo = engine_repo
        if repo is None:
            engine_root = resolve_engine_root()
            probe = subprocess.run(
                ["git", "-C", str(engine_root), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=timeout,
            )
            if probe.returncode != 0:
                return result
            repo = probe.stdout.strip()
        current_file = os.path.join(repo, "VERSION")
        try:
            with open(current_file, encoding="utf-8") as handle:
                result["current"] = f"v{handle.read().strip()}"
        except OSError:
            pass
        latest = _resolve_newest_tag(repo, timeout=timeout)
        if latest is not None:
            result["latest"] = latest
            result["has_update"] = bool(result.get("current")) and latest.removeprefix("v") != result["current"].removeprefix("v") and _tag_newer(
                latest, result.get("current", "")
            )
        _write_state(result)
    except Exception:  # noqa: BLE001 - notifier never fails the shell
        pass
    try:
        from nexgen_core.depwatch import run_depwatch

        run_depwatch()
    except Exception:  # noqa: BLE001 - notifier never fails the shell
        pass
    return result


def _tag_newer(latest: str, current: str) -> bool:
    import re

    semver = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
    newer = semver.fullmatch(latest.strip())
    older = semver.fullmatch(current.strip())
    if not newer or not older:
        return False
    return tuple(int(g) for g in newer.groups()) > tuple(int(g) for g in older.groups())


def _spawn_background_refresh() -> None:
    """Re-checks origin without holding the shell: detached, silent, cheap."""
    try:
        nexgen = shutil.which("nexgen")
        if nexgen:
            entry = [nexgen, "tool", "update-notifier", "--refresh-cache"]
        else:
            # No shim on PATH (fresh install, PATH not reloaded): run the
            # checkout's own entry instead of letting the cache rot silent.
            cli_entry = Path(__file__).resolve().parents[1] / "cli" / "__init__.py"
            if not cli_entry.is_file():
                return
            entry = [sys.executable, str(cli_entry), "tool", "update-notifier", "--refresh-cache"]
        if os.name == "nt":
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            subprocess.Popen(
                entry, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, creationflags=creationflags,
                cwd=str(resolve_home()),
            )
        else:
            subprocess.Popen(
                entry, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, start_new_session=True,
                cwd=str(resolve_home()),
            )
    except (OSError, ValueError):
        pass


def _cache_fresh(state: dict) -> bool:
    try:
        return (time.time() - float(state.get("checked_at", 0))) < CACHE_STALE_AFTER_HOURS * 3600
    except (TypeError, ValueError):
        return False


def _cache_usable(state: dict) -> bool:
    try:
        age_days = (time.time() - float(state.get("checked_at", 0))) / 86400
    except (TypeError, ValueError):
        return False
    return age_days < CACHE_TOO_OLD_TO_NAG_DAYS and bool(state.get("latest")) and bool(state.get("current"))

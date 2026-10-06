"""The liveness heartbeat and dependency watch for NeXgen Engine v2.

Heartbeat duties (hourly, independent of the Guard: it runs without ever
holding the guard lock):
1. Liveness: checks that the Guard made it to the end recently (file
   agent-guard-liveness). This file answers exactly ONE question and must
   never be shared with the Megaphone's alert debounce: sharing it is what
   froze liveness behind the debounce.
2. Dependency Watch: inspects pinned third-party dependencies upstream and
   writes third-party-upgrades.md to the state folder. It never applies
   anything and never notifies.
3. Unattended Self-Upgrader: applies a released patch bump, if there is one,
   without asking. It refuses a minor or major bump on its own.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from nexgen_core.depwatch import run_depwatch
from nexgen_core.i18n import t
from nexgen_core.megaphone import Megaphone
from nexgen_core.paths import (
    resolve_engine_root,
    resolve_home,
    resolve_state_dir,
    resolve_vault_data,
)
from nexgen_core.updater import (
    EXIT_REFUSED as UPDATE_EXIT_REFUSED,
    EXIT_ROLLBACK_FAILED as UPDATE_EXIT_ROLLBACK_FAILED,
    EXIT_ROLLED_BACK as UPDATE_EXIT_ROLLED_BACK,
    EngineUpdater,
)

logger = logging.getLogger(__name__)

LIVENESS_FILE_NAME = "agent-guard-liveness"
MAX_LIVENESS_AGE_HOURS = 2.5


def _just_booted(grace_seconds: float) -> bool:
    """True when the system itself started more recently than the grace window.

    Tells "guard never ran since boot" apart from "guard stalled for hours":
    only the second deserves an alert. Linux-only signal (`/proc/uptime`);
    anywhere else it returns False and alerting behaves as before.
    """
    try:
        with open("/proc/uptime", encoding="utf-8") as handle:
            uptime = float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return False
    return uptime < grace_seconds


class Heartbeat:
    """Manager for the hourly heartbeat."""

    def __init__(
        self,
        state_dir: Path | None = None,
        vault_data: Path | None = None,
        engine_root: Path | None = None,
        home: Path | None = None,
    ) -> None:
        # The home has to be threaded through, not looked up again. A caller
        # working under a sandbox home was still writing this machine's
        # liveness and taking this machine's lock, because it passed the
        # vault and the engine and left the state directory to resolve
        # itself from the environment.
        self.home = resolve_home(home)
        self.state_dir = resolve_state_dir(self.home, override=state_dir)
        self.vault_data = resolve_vault_data(self.home, override=vault_data)
        self.engine_root = resolve_engine_root(self.home, override=engine_root)
        self.megaphone = Megaphone(state_dir=self.state_dir)
        self.liveness_file = self.state_dir / LIVENESS_FILE_NAME

    def record_liveness(self, warnings: int = 0, failed_phases: Sequence[str] = ()) -> None:
        """Records the successful completion of a Guard cycle, and by whom.

        The version is written alongside the timestamp because otherwise
        nobody can answer "is this machine still on the old release?" — and
        that is the question that decides when transitional compatibility
        can be deleted. Without it the answer is a guess, and the
        compatibility stays forever.

        The format stays a first line that parses as a float, so a previous
        version reading this file keeps working: it reads the first line and
        ignores the rest. The warning count rides a third line for the same
        reason: a cycle that completed with degraded phases is still a
        completed cycle, but the monitor should say so. The phases that failed
        ride a fourth line, only when there are any: the guard now runs every
        phase even after one fails, so reaching the end no longer means every
        phase worked, and "alive" and "healthy" are different answers.
        """
        from nexgen_core import __version__
        from nexgen_core.files import atomic_write_text

        self.state_dir.mkdir(parents=True, exist_ok=True)
        text = f"{time.time()}\n{__version__}\nWARN={int(warnings)}\n"
        if failed_phases:
            text += "FAIL=" + ",".join(failed_phases) + "\n"
        atomic_write_text(self.liveness_file, text)

    def recorded_failed_phases(self) -> list[str]:
        """Phases that failed in the last recorded cycle (empty when none or unrecorded)."""
        try:
            lines = self.liveness_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines[2:]:
            if line.strip().startswith("FAIL="):
                return [p for p in line.strip().split("=", 1)[1].split(",") if p]
        return []

    def recorded_warnings(self) -> int | None:
        """Warning count of the last recorded cycle, None when unrecorded."""
        if not self.liveness_file.is_file():
            return None
        try:
            lines = self.liveness_file.read_text(encoding="utf-8").splitlines()
            for line in lines[2:]:
                if line.strip().startswith("WARN="):
                    return int(line.strip().split("=", 1)[1])
        except (OSError, ValueError):
            return None
        return None

    def recorded_version(self) -> str | None:
        """Which engine last completed a cycle here, if it said so.

        `None` means the file was written by a version that did not record
        one — which is itself the answer: this machine is behind.
        """
        try:
            lines = self.liveness_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        return lines[1].strip() if len(lines) >= 2 and lines[1].strip() else None

    def check_liveness(self) -> tuple[bool, str]:
        """Checks whether the Guard has run within the expected window."""
        if not self.liveness_file.is_file():
            return False, t("No guard cycle has been recorded yet")

        try:
            # First line only: later versions may append fields after it,
            # and this reader must not care.
            first = self.liveness_file.read_text(encoding="utf-8").splitlines()[0]
            last_ts = float(first.strip())
            elapsed = time.time() - last_ts
            if elapsed > MAX_LIVENESS_AGE_HOURS * 3600:
                if _just_booted(MAX_LIVENESS_AGE_HOURS * 3600):
                    # Fresh boot or wake: the guard has not had a chance to
                    # run yet. Alerting here is the suspend-spam that cries
                    # stalled at every laptop wake.
                    return True, t(
                        "Guard starting up (last cycle {minutes:.0f} minutes ago, machine just started)",
                        minutes=elapsed / 60,
                    )
                hours = elapsed / 3600
                msg = t("The sync cycle has been stalled for {hours:.1f} hours.", hours=hours)
                self.megaphone.send_alert(
                    title=t("Agent sync is not running"),
                    message=msg,
                    action=t("Run 'agent-sync apply' in the terminal to check the status."),
                    alert_key="guard_stale"
                )
                return False, msg
            msg = t("Guard active (last completed {minutes:.0f} minutes ago)", minutes=elapsed / 60)
            warns = self.recorded_warnings()
            if warns:
                msg += " " + t("(last cycle completed with {count} warnings)", count=warns)
            failed = self.recorded_failed_phases()
            if failed:
                msg += " " + t("(last cycle: these phases failed: {phases})", phases=", ".join(failed))
            return True, msg
        except Exception as exc:  # noqa: BLE001 - failure is returned, never raises
            # A corrupt liveness file blinds self-monitoring: alert once
            # (debounced) instead of returning a silent False nobody acts on.
            self.megaphone.send_alert(
                title=t("Agent sync self-monitoring is blind"),
                message=t("The liveness file cannot be read ({error}); fix or delete it.", error=exc),
                action=t("Run 'agent-sync apply' in the terminal to check the status."),
                alert_key="guard_liveness_corrupt",
            )
            return False, t("Error reading liveness: {error}", error=exc)

    def run_dependency_watch(self) -> dict[str, Any]:
        """Inspects pinned third-party dependencies upstream, judges the
        stale ones with deterministic rules, and raises the vetted pins
        without asking. Held items never move here. Never notifies: a
        failure is missed maintenance, not a reason to stop the heartbeat."""
        try:
            result = run_depwatch(vault_data=self.vault_data, state_dir=self.state_dir)
            answer: dict[str, Any] = {
                "ok": True, "wrote": result.wrote,
                "stale": sum(f.stale for f in result.findings),
            }
            try:
                from nexgen_core.thirdparty_guard import run_guardian

                scopes = self._skill_scopes()
                answer["guard"] = run_guardian(
                    result.findings, self.state_dir, skill_scopes=scopes,
                )
            except Exception as exc:  # noqa: BLE001 - failure is returned, never raises
                answer["guard"] = {"ok": False, "error": str(exc)}
            try:
                from nexgen_core.thirdparty_bump import auto_apply, read_guard_payload

                payload = read_guard_payload(self.state_dir)
                answer["auto_applied"] = auto_apply(
                    payload or {}, self.vault_data, self.home, self.state_dir,
                )
            except Exception as exc:  # noqa: BLE001 - failure is returned, never raises
                answer["auto_applied"] = {"ok": False, "error": str(exc)}
            return answer
        except Exception as exc:  # noqa: BLE001 - failure is returned, never raises
            return {"ok": False, "error": str(exc)}

    def _skill_scopes(self) -> dict[str, str]:
        """Vendored subpath per github skill, so the guardian only judges
        the bytes the engine actually materializes. Unknown skills default
        to the whole repo, which is the conservative side."""
        try:
            from nexgen_core.config import load_skills_manifest
            from nexgen_core.paths import skills_manifest

            manifest = skills_manifest(self.vault_data)
            if not manifest.is_file():
                return {}
            data = load_skills_manifest(manifest).get("skills", {})
            scopes: dict[str, str] = {}
            for name, entry in data.items():
                if isinstance(entry, dict) and entry.get("origin") == "github":
                    scopes[str(name)] = str(entry.get("path") or ".")
            return scopes
        except Exception as exc:  # noqa: BLE001 - unreadable manifest means whole-repo scopes, never a beat crash
            logger.debug("skill scopes fallback to whole-repo (%s)", type(exc).__name__)
            return {}

    def run_self_upgrade(self) -> dict[str, Any]:
        """Attempts an unattended upgrade, capped at a patch bump by the
        updater. Uses THIS Heartbeat's vault/engine, not the process
        environment, so a Heartbeat built for tests never touches the host's
        real installation."""
        try:
            environ = {
                **os.environ,
                "AGENT_ENGINE_ROOT": str(self.engine_root),
                "AGENT_VAULT_DATA": str(self.vault_data),
                # The updater takes the host lock and keeps its rejected-release
                # memory in this install's state directory, not the process's.
                "AGENT_STATE_DIR": str(self.state_dir),
            }
            exit_code = EngineUpdater.main(["--unattended"], environ=environ)
            self._alert_on_update_outcome(exit_code)
            return {"ok": exit_code == 0, "exit_code": exit_code}
        except Exception as exc:  # noqa: BLE001 - failure is returned, never raises
            return {"ok": False, "error": str(exc)}

    def _alert_on_update_outcome(self, exit_code: int) -> None:
        """Tells the person about the three update outcomes that need them.

        Other non-zero codes are the quiet kind (a release that cannot be
        verified, a busy lock, a jump the ceiling declines): they come back
        every hour by design and would only train people to ignore the alert.
        """
        if exit_code == UPDATE_EXIT_REFUSED:
            title = t("An update was refused")
            message = t("A new release is not signed by a key this install trusts, so nothing was installed.")
            action = t("Run 'nexgen-update --check' and look at the release before doing anything else.")
            key = "update_refused"
        elif exit_code == UPDATE_EXIT_ROLLED_BACK:
            title = t("An automatic update failed and was undone")
            message = t("The machine is back on the version that worked. That release is skipped until you update by hand.")
            action = t("Run 'nexgen-update' interactively to see why it failed.")
            key = "update_rolled_back"
        elif exit_code == UPDATE_EXIT_ROLLBACK_FAILED:
            title = t("An automatic update failed and could not be undone")
            message = t("The engine may be half-updated. It was left as it is.")
            action = t("Run 'nexgen doctor --summary' and follow the recovery printed by 'nexgen-update'.")
            key = "update_rollback_failed"
        else:
            return
        try:
            self.megaphone.send_alert(title=title, message=message, action=action, alert_key=key)
        except Exception as exc:  # noqa: BLE001 - an alert that fails must not fail the beat
            logger.debug("update alert not sent (%s)", type(exc).__name__)

    def run_beat(self) -> dict[str, Any]:
        """Runs the full heartbeat cycle: the liveness question, then the two
        maintenance tasks the contract assigns to this spot because it runs
        regularly without holding the guard lock."""
        liveness_ok, liveness_msg = self.check_liveness()
        self_upgrade = self.run_self_upgrade()
        try:
            from nexgen_core.tools.update_notifier import refresh_update_cache

            probe = subprocess.run(
                ["git", "-C", str(self.engine_root), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=20,
            )
            if probe.returncode == 0 and probe.stdout.strip():
                refresh_update_cache(probe.stdout.strip())
        except Exception as exc:  # noqa: BLE001 - offline cache refresh never fails the beat
            logger.debug("background update-cache refresh skipped (%s)", type(exc).__name__)
        return {
            "liveness_ok": liveness_ok,
            "liveness_msg": liveness_msg,
            "dependency_watch": self.run_dependency_watch(),
            "self_upgrade": self_upgrade,
            "timestamp": time.time(),
        }

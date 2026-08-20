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

import os
import time
from pathlib import Path
from typing import Any

from nexgen_core.depwatch import run_depwatch
from nexgen_core.i18n import t
from nexgen_core.megaphone import Megaphone
from nexgen_core.paths import resolve_engine_root, resolve_state_dir, resolve_vault_data
from nexgen_core.updater import EngineUpdater

LIVENESS_FILE_NAME = "agent-guard-liveness"
MAX_LIVENESS_AGE_HOURS = 2.5


class Heartbeat:
    """Manager for the hourly heartbeat."""

    def __init__(
        self,
        state_dir: Path | None = None,
        vault_data: Path | None = None,
        engine_root: Path | None = None,
    ) -> None:
        self.state_dir = resolve_state_dir(override=state_dir)
        self.vault_data = resolve_vault_data(override=vault_data)
        self.engine_root = resolve_engine_root(override=engine_root)
        self.megaphone = Megaphone(state_dir=self.state_dir)
        self.liveness_file = self.state_dir / LIVENESS_FILE_NAME

    def record_liveness(self) -> None:
        """Records the successful completion of a Guard cycle."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.liveness_file.write_text(str(time.time()), encoding="utf-8")

    def check_liveness(self) -> tuple[bool, str]:
        """Checks whether the Guard has run within the expected window."""
        if not self.liveness_file.is_file():
            return False, t("No guard cycle has been recorded yet")

        try:
            last_ts = float(self.liveness_file.read_text(encoding="utf-8").strip())
            elapsed = time.time() - last_ts
            if elapsed > MAX_LIVENESS_AGE_HOURS * 3600:
                hours = elapsed / 3600
                msg = t("The sync cycle has been stalled for {hours:.1f} hours.", hours=hours)
                self.megaphone.send_alert(
                    title=t("Agent sync is not running"),
                    message=msg,
                    action=t("Run 'agent-sync apply' in the terminal to check the status."),
                    alert_key="guard_stale"
                )
                return False, msg
            return True, t("Guard active (last completed {minutes:.0f} minutes ago)", minutes=elapsed / 60)
        except Exception as exc:
            return False, t("Error reading liveness: {error}", error=exc)

    def run_dependency_watch(self) -> dict[str, Any]:
        """Inspects pinned third-party dependencies upstream. Never applies
        anything and never notifies: a failure here is missed maintenance,
        not a reason to stop the rest of the heartbeat."""
        try:
            result = run_depwatch(vault_data=self.vault_data, state_dir=self.state_dir)
            return {"ok": True, "wrote": result.wrote, "stale": sum(f.stale for f in result.findings)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

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
            }
            exit_code = EngineUpdater.main(["--unattended"], environ=environ)
            return {"ok": exit_code == 0, "exit_code": exit_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def run_beat(self) -> dict[str, Any]:
        """Runs the full heartbeat cycle: the liveness question, then the two
        maintenance tasks the contract assigns to this spot because it runs
        regularly without holding the guard lock."""
        liveness_ok, liveness_msg = self.check_liveness()
        return {
            "liveness_ok": liveness_ok,
            "liveness_msg": liveness_msg,
            "dependency_watch": self.run_dependency_watch(),
            "self_upgrade": self.run_self_upgrade(),
            "timestamp": time.time(),
        }

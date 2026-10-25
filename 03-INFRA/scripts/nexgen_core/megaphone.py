"""The megaphone: the single notification and alert surface for NeXgen Engine v2.

Contract invariant 7:
There exists exactly one alert surface, with debounce, consistent formatting,
and transport fallback.
No component notifies on its own.

The first transport is OpenClaw's own CLI. Where OpenClaw runs, the person
already has a channel configured there, Telegram among them, and a second
message handed straight to the Bot API from the engine would be the same alert
twice: once through the harness and once around it. OpenClaw is a door of the
engine, not a destination beside it.
"""
from __future__ import annotations

import html
import json
import logging
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexgen_core import notify
from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_state_dir
from nexgen_core.processes import SHIM_SUFFIXES, no_window_kwargs, shim_node_argv

STATE_FILE_NAME = "agent-healthcheck.state"
DEFAULT_DEBOUNCE_HOURS = 4.0

#: The CLI by name, not by path, so PATH decides which install speaks.
OPENCLAW_COMMAND = "openclaw"

#: The file the npm package declares as its `bin`, sitting beside the shim that
#: npm also installs. Node runs it as a program; a `.cmd` cannot be one.
OPENCLAW_ENTRY = "node_modules/openclaw/openclaw.mjs"

#: A send that has not answered in twenty seconds is not going to answer. The
#: alert then belongs to the transports after it, not to a hung child process.
OPENCLAW_TIMEOUT_SECONDS = 20.0

#: `(argv, timeout) -> completed process`: the executor a test replaces with a recorder.
Run = Callable[[list[str], float], Any]

#: `name -> path or None`: where the command is, replaceable for the same reason.
Which = Callable[[str], "str | None"]


logger = logging.getLogger(__name__)


def default_run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run OpenClaw's CLI: argv only, never a shell string, stdin closed.

    The argv arrives as a program plus arguments, because `openclaw_launch` has
    already turned npm's Windows shim into node and the package entry. Nothing
    here may route it through `cmd.exe`: the alert text is an error message or a
    file name from outside, and a command interpreter reads `&`, `|`, `^` and
    `%` inside it.
    """
    return subprocess.run(
        list(argv),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
        **no_window_kwargs(),
    )


def openclaw_launch(found: str, which: Which) -> list[str] | None:
    """The program and arguments that start OpenClaw, or None when it cannot start.

    `shutil.which` answers with npm's `openclaw.cmd` on Windows, and Windows
    starts a batch file only through `cmd.exe`, which re-reads `&`, `|`, `^` and
    `%` in whatever it is given: with an alert that carries arbitrary text that
    is injection, not a lost message. The package declares `openclaw.mjs` as its
    `bin` and puts it beside the shim, so node runs it as a program with
    arguments. The rule is npm's, not OpenClaw's, and it lives in
    `nexgen_core.processes` beside the shims the engine starts itself. When that
    entry or node itself is missing, OpenClaw is not a transport on this machine
    and the next one takes the alert.
    """
    shim = Path(found)
    if shim.suffix.lower() not in SHIM_SUFFIXES:
        return [OPENCLAW_COMMAND]
    return shim_node_argv(found, OPENCLAW_ENTRY, [], which)


class Megaphone:
    """Unified manager for human notifications and alerts."""

    def __init__(
        self,
        state_dir: Path | None = None,
        *,
        run: Run | None = None,
        which: Which | None = None,
    ) -> None:
        self.state_dir = resolve_state_dir(override=state_dir)
        self.state_file = self.state_dir / STATE_FILE_NAME
        # Both are injectable because the only honest test of "would have sent
        # through OpenClaw" must not launch the person's real OpenClaw.
        self._run: Run = run if run is not None else default_run
        self._which: Which = which if which is not None else shutil.which

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # noqa: BLE001 - corrupt state resets debounce, never crashes alerts
            logger.debug("ignoring corrupt megaphone state %s (%s)", self.state_file, type(exc).__name__)
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.state_file, json.dumps(state, indent=2))

    def should_notify(self, alert_key: str, debounce_hours: float = DEFAULT_DEBOUNCE_HOURS) -> bool:
        """Checks whether the alert should be sent, or is within its debounce window."""
        state = self._load_state()
        last_sent = state.get(alert_key, 0.0)
        now = time.time()
        if now - last_sent < debounce_hours * 3600:
            return False
        return True

    def mark_notified(self, alert_key: str) -> None:
        """Records that the alert was sent, for debounce purposes."""
        state = self._load_state()
        state[alert_key] = time.time()
        self._save_state(state)

    def send_alert(self, title: str, message: str, action: str | None = None, alert_key: str | None = None) -> bool:
        """Sends an alert through whichever channel can carry it.

        Returns True when the alert was delivered OR deliberately suppressed
        by debounce (both mean "handled, do not retry"): only an undelivered
        alert with no channel at all returns False. Callers must not read
        True as proof of delivery to a phone screen.

        The transport order lives in `_deliver`: one transport ends the chain,
        and only when none of them can carry the alert does the log keep it.
        """
        key = alert_key or f"{title}:{message[:50]}"
        if not self.should_notify(key):
            return True

        sent = self._deliver(title, message, action)
        if sent:
            self.mark_notified(key)
        else:
            # Nothing could carry it. The log is the last place it can exist,
            # and staying silent here is how an alert disappears completely.
            logger.warning("alert not delivered: %s — %s", title, message)
        return sent

    def _deliver(self, title: str, message: str, action: str | None) -> bool:
        """The transport order: the first one that carries the alert ends it."""
        # 0. OpenClaw, where it is installed and knows a destination. Its
        #    channels render plain text, so the Telegram HTML below belongs to
        #    that transport and must not leak into this one.
        sent = False
        route = self._openclaw_route()
        if route is not None:
            launch, channel, target = route
            sent = self._send_openclaw(launch, channel, target, self._plain_text(title, message, action))
        delivered_by_openclaw = sent

        # 1. Telegram direct: the fallback for a machine without OpenClaw, and
        #    nothing more. It does not fire behind a delivered OpenClaw alert.
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if bot_token and chat_id and not sent:
            sent = self._send_telegram(bot_token, chat_id, self._html_text(title, message, action))

        # 2. Generic webhook. It does not fire behind a delivered OpenClaw alert
        #    either: one alert reaches one surface, and a webhook that repeats a
        #    message the harness already carried is the same alarm arriving twice.
        webhook_url = os.environ.get("AGENT_ALERT_WEBHOOK_URL")
        if webhook_url and not delivered_by_openclaw:
            sent = self._send_webhook(webhook_url, {"title": title, "message": message, "action": action}) or sent

        # 3. The screen in front of the person. Last, because a message that
        # reached their phone does not need a toast too — but first in
        # importance for anyone who configured neither of the above, who until
        # now received nothing at all.
        if not sent and notify.desktop_is_available():
            body = message if not action else f"{message}\n\n{t('Action required:')} {action}"
            sent = notify.send_desktop(title, body)
        return sent

    def _html_text(self, title: str, message: str, action: str | None) -> str:
        """The alert as Telegram renders it: escaped, with the markup it reads."""
        text = f"🚨 <b>{html.escape(title)}</b>\n\n{html.escape(message)}"
        if action:
            text += f"\n\n👉 <b>{t('Action required:')}</b>\n<code>{html.escape(action)}</code>"
        return text

    def _plain_text(self, title: str, message: str, action: str | None) -> str:
        """The alert as the text a channel renders: title, message, what to do."""
        text = f"🚨 {title}\n\n{message}"
        if action:
            text += f"\n\n{t('Action required:')} {action}"
        return text

    def _openclaw_route(self) -> tuple[list[str], str, str] | None:
        """How OpenClaw would carry this, or None when it cannot carry it.

        Both halves are required. A CLI that is installed but has no
        destination would fail on every alert, and a destination without the
        CLI is a setting nobody can act on. A machine that already told the
        engine where its Telegram chat is does not have to say it twice. The
        launch prefix is resolved here as well: a Windows shim that cannot
        become a program is as good as no OpenClaw at all, and finding that out
        before the send is what keeps the chain moving.
        """
        found = self._which(OPENCLAW_COMMAND)
        if found is None:
            return None
        launch = openclaw_launch(found, self._which)
        if launch is None:
            return None
        channel = os.environ.get("NEXGEN_ALERT_CHANNEL", "").strip()
        if not channel:
            return None
        target = os.environ.get("NEXGEN_ALERT_TARGET", "").strip()
        if not target and channel.lower() == "telegram":
            target = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not target:
            return None
        return launch, channel, target

    def _send_openclaw(self, launch: list[str], channel: str, target: str, text: str) -> bool:
        """Hands the alert to `openclaw message send`, and says whether it left.

        The text is one element of the list. It is never quoted into a command
        line, because the characters that would need quoting are exactly the
        ones an interpreter would act on.
        """
        command = [
            *launch, "message", "send",
            "--channel", channel,
            "--target", target,
            "--message", text,
        ]
        try:
            result = self._run(command, OPENCLAW_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - a wedged or missing CLI falls through, never raises
            logger.debug("openclaw send failed (%s)", type(exc).__name__)
            return False
        return getattr(result, "returncode", None) == 0

    def _send_telegram(self, token: str, chat_id: str, text: str) -> bool:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as exc:  # noqa: BLE001 - transport failure falls back to webhook/desktop, never raises
            logger.debug("telegram send failed (%s)", type(exc).__name__)
            return False

    def _send_webhook(self, url: str, data: dict[str, Any]) -> bool:
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 201, 204)
        except Exception as exc:  # noqa: BLE001 - transport failure falls back to desktop, never raises
            logger.debug("webhook send failed (%s)", type(exc).__name__)
            return False

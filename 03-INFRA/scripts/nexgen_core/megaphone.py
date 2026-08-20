"""Il megafono: unica superficie di notifica e allarme per NeXgen Engine v2.

Invariante 7 del contratto:
Esiste esattamente una sola superficie di allarme con antirimbalzo (debounce),
formattazione coerente e fallback di trasporto.
Nessun componente notifica per conto proprio.
"""
from __future__ import annotations

import html
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from nexgen_core.paths import resolve_state_dir

STATE_FILE_NAME = "agent-healthcheck.state"
DEFAULT_DEBOUNCE_HOURS = 4.0


class Megaphone:
    """Gestore unificato delle notifiche umane e degli allarmi."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = resolve_state_dir(override=state_dir)
        self.state_file = self.state_dir / STATE_FILE_NAME

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def should_notify(self, alert_key: str, debounce_hours: float = DEFAULT_DEBOUNCE_HOURS) -> bool:
        """Verifica se l'allarme deve essere inviato o è in periodo di antirimbalzo."""
        state = self._load_state()
        last_sent = state.get(alert_key, 0.0)
        now = time.time()
        if now - last_sent < debounce_hours * 3600:
            return False
        return True

    def mark_notified(self, alert_key: str) -> None:
        """Registra l'invio dell'allarme per l'antirimbalzo."""
        state = self._load_state()
        state[alert_key] = time.time()
        self._save_state(state)

    def send_alert(self, title: str, message: str, action: str | None = None, alert_key: str | None = None) -> bool:
        """Invia un allarme attraverso i canali configurati (Telegram, Webhook o log)."""
        key = alert_key or f"{title}:{message[:50]}"
        if not self.should_notify(key):
            return True

        safe_title = html.escape(title)
        safe_msg = html.escape(message)
        text = f"🚨 <b>{safe_title}</b>\n\n{safe_msg}"
        if action:
            safe_act = html.escape(action)
            text += f"\n\n👉 <b>Azione richiesta:</b>\n<code>{safe_act}</code>"

        sent = False
        # 1. Telegram
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if bot_token and chat_id:
            sent = self._send_telegram(bot_token, chat_id, text)

        # 2. Webhook generico
        webhook_url = os.environ.get("AGENT_ALERT_WEBHOOK_URL")
        if webhook_url:
            sent = self._send_webhook(webhook_url, {"title": title, "message": message, "action": action}) or sent

        if sent:
            self.mark_notified(key)
        return sent

    def _send_telegram(self, token: str, chat_id: str, text: str) -> bool:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _send_webhook(self, url: str, data: dict[str, Any]) -> bool:
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 201, 204)
        except Exception:
            return False

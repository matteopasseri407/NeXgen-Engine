"""update-notifier terminal prompt + shell-check. Reads state, never writes boot. Owned here, re-exported by update_notifier for compat."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path



from .notifier_state import (
    _cache_fresh, _cache_usable, _dismissed_today, _is_throttled,
    _read_state, _record_prompt_time, _skills_report_file,
    _spawn_background_refresh,
)
from .notifier_skills import _confirm_skills_shown, _skills_notice

def _call_logo_path() -> str:
    """Facade-aware logo: tests patch update_notifier._logo_path."""
    try:
        from . import update_notifier as _fac

        func = getattr(_fac, "_logo_path", None)
        if func is not None and func is not _logo_path:
            return func()
    except ImportError:
        pass
    return _logo_path()


def _call_prompt_user(current: str, latest: str, notes_hint: str = "") -> bool:
    try:
        from . import update_notifier as _fac

        func = getattr(_fac, "_prompt_user", None)
        if func is not None and func is not _prompt_user:
            return func(current, latest, notes_hint)
    except ImportError:
        pass
    return _prompt_user(current, latest, notes_hint)



def _logo_path() -> str:
    """Engine logo, only for dialogs that actually communicate something.

    Resolves ``assets/nexgen-logo.jpg`` from the engine checkout; empty when
    the checkout ships without assets, in which case callers fall back to the
    stock ``system-software-update`` icon. Nothing is ever installed as a
    fixed icon: the logo travels on the notification itself.
    """
    try:
        from nexgen_core.paths import resolve_engine_root

        logo = Path(resolve_engine_root()) / "assets" / "nexgen-logo.jpg"
        return str(logo) if logo.is_file() else ""
    except Exception:  # noqa: BLE001 - notifier never fails the shell
        return ""


def _prompt_linux(current: str, latest: str, notes_hint: str = "") -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False

    text = (
        f"<b>Nuova versione disponibile: {latest}</b> (versione attuale: {current})\n\n"
        "Desideri aggiornare adesso NeXgen Engine?"
    )
    if notes_hint:
        text += f"\n\n{notes_hint}"

    if shutil.which("zenity"):
        cmd = [
            "zenity", "--question",
            "--title=NeXgen Engine Update",
            f"--text={text}",
            "--ok-label=Aggiorna ora",
            "--cancel-label=Più tardi",
            "--width=420",
            f"--window-icon={_call_logo_path() or 'system-software-update'}",
        ]
        proc = subprocess.run(cmd, check=False)
        return proc.returncode == 0

    if shutil.which("kdialog"):
        cmd = ["kdialog", "--yesno", text.replace("<b>", "").replace("</b>", ""), "--title", "NeXgen Engine Update"]
        if _call_logo_path():
            cmd += ["--icon", _call_logo_path()]
        proc = subprocess.run(cmd, check=False)
        return proc.returncode == 0

    return False


def _prompt_windows(current: str, latest: str, notes_hint: str = "") -> bool:
    text = (
        f"Nuova versione di NeXgen Engine disponibile: {latest}\n"
        f"(Versione attualmente installata: {current})\n\n"
        "Desideri aggiornare adesso il motore?"
    )
    if notes_hint:
        text += f"\n\n{notes_hint}"
    title = "NeXgen Engine Update"
    MB_YESNO = 0x00000004
    MB_ICONINFORMATION = 0x00000040
    IDYES = 6

    try:
        res = ctypes.windll.user32.MessageBoxW(0, text, title, MB_YESNO | MB_ICONINFORMATION)
        return res == IDYES
    except Exception:  # noqa: BLE001 - notifier never fails the shell
        return False


def _prompt_user(current: str, latest: str, notes_hint: str = "") -> bool:
    if os.name == "nt":
        return _prompt_windows(current, latest, notes_hint)
    return _prompt_linux(current, latest, notes_hint)


def _notify_success(latest: str) -> None:
    import contextlib

    if os.name == "nt":
        with contextlib.suppress(Exception):
            ctypes.windll.user32.MessageBoxW(
                0,
                f"NeXgen Engine aggiornato con successo alla versione {latest}!",
                "NeXgen Engine Update",
                0x00000000 | 0x00000040
            )
    else:
        icon = _call_logo_path() or "system-software-update"
        if shutil.which("notify-send"):
            subprocess.run([
                "notify-send", "NeXgen Engine",
                f"Aggiornamento a {latest} completato con successo!",
                f"--icon={icon}",
            ], check=False)
        elif shutil.which("zenity"):
            subprocess.run([
                "zenity", "--info",
                "--title=NeXgen Engine",
                f"--text=Aggiornamento a <b>{latest}</b> completato con successo!",
                "--width=340",
                f"--window-icon={icon}",
            ], check=False)


def _run_update() -> bool:
    try:
        from nexgen_core.updater import EngineUpdater
        return EngineUpdater.main(["--yes"]) == 0
    except Exception:  # noqa: BLE001 - notifier never fails the shell
        return False


def _notes_hint() -> str:
    return "Note di rilascio: `nexgen update --check`."


def cmd_check(force: bool = False) -> int:
    try:
        from nexgen_core.updater import EngineUpdater
        has_update, current, latest = EngineUpdater.check_updates()
    except Exception as exc:  # noqa: BLE001 - notifier never fails the shell
        print(f"[update-notifier] check failed: {exc}", file=sys.stderr)
        return 1

    if has_update:
        if force or not (_dismissed_today(latest) or _is_throttled()):
            _record_prompt_time(latest)
            wants_update = _call_prompt_user(current, latest, _notes_hint())

            if wants_update:
                ok = _run_update()
                if ok:
                    _notify_success(latest)
                else:
                    if os.name != "nt" and shutil.which("zenity"):
                        subprocess.run(["zenity", "--error", "--title=NeXgen Engine", "--text=Errore durante l'aggiornamento. Riprova con 'nexgen update' dal terminale."], check=False)
                    return 1

    _check_skills_gui(force=force)
    return 0


def _check_skills_gui(force: bool = False) -> None:
    """GUI lane twin for third-party skills/MCP: notice only, never apply.

    Same message as the shell lane, no questions anywhere.
    """
    try:
        message = _skills_notice(mark=False)
        if not message:
            return
        if force:
            print(message)
            _confirm_skills_shown()
            return
        text = f"{message}\nDettagli: {_skills_report_file()}"
        if os.name == "nt":
            try:
                import contextlib

                with contextlib.suppress(Exception):
                    ctypes.windll.user32.MessageBoxW(
                        0, text, "NeXgen Engine — terze parti", 0x00000000 | 0x00000040
                    )
                _confirm_skills_shown()
            except Exception:  # noqa: BLE001 - notifier never fails the shell
                print(text)
                _confirm_skills_shown()
        elif shutil.which("zenity") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            proc = subprocess.run([
                "zenity", "--info",
                "--title=NeXgen Engine — terze parti",
                f"--text={text}",
                "--width=480",
                f"--window-icon={_call_logo_path() or 'system-software-update'}",
            ], check=False)
            if proc.returncode == 0:
                _confirm_skills_shown()
        else:
            print(text)
            _confirm_skills_shown()
    except Exception as exc:  # noqa: BLE001 - notifier never fails the shell
        print(f"[update-notifier] skills check failed: {exc}", file=sys.stderr)


def cmd_shell_check() -> int:
    """The shell-startup fast path: file read only, prompt at most once per
    day per version, background refresh when stale. Returns 0 always (a
    shell hook must never break a shell startup)."""
    try:
        return _shell_check()
    except Exception as exc:  # noqa: BLE001 - notifier never fails the shell
        print(f"[update-notifier] shell check failed: {exc}", file=sys.stderr)
        return 0


def _shell_check() -> int:
    state = _read_state()
    if not _cache_fresh(state):
        _spawn_background_refresh()
    if _cache_usable(state) and state.get("has_update"):
        _shell_check_engine(state)
    _shell_check_skills()
    return 0


def _shell_check_engine(state: dict) -> None:
    current = str(state.get("current", ""))
    latest = str(state.get("latest", ""))
    if not current or not latest or _dismissed_today(latest):
        return
    if not sys.stdin.isatty():
        return
    try:
        answer = input(
            f"\nNeXgen Engine: {latest} disponibile (installata: {current}). "
            "Nota: `nexgen update --check`. Aggiorna ora? [s/N] "
        )
    except EOFError:
        return
    _record_prompt_time(latest)
    if answer.strip().lower() in {"s", "si", "y", "yes"}:
        print(f"Avvio `nexgen update` verso {latest}...")
        try:
            os.execvp("nexgen", ["nexgen", "update", "--target", latest.removeprefix("v")])
        except OSError as exc:
            print(f"[update-notifier] cannot launch `nexgen update`: {exc}", file=sys.stderr)


def _shell_check_skills() -> None:
    """Second lane of the same shell hook: says what moved, asks nothing.

    Vetted pins already moved on their own in the hourly beat; this lane
    only announces them once, plus one quiet line for whatever is held.
    """
    try:
        if not sys.stdin.isatty():
            return
        message = _skills_notice()
        if message:
            print(f"\n{message}")
    except Exception as exc:  # noqa: BLE001 - notifier never fails the shell
        print(f"[update-notifier] skills check failed: {exc}", file=sys.stderr)

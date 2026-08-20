#!/usr/bin/env python3
"""Determinazione deterministica di data, ora e sincronizzazione orologio per NeXgen Engine v2.

Preserva al 100% il contratto a 13 campi di agent-now:
- local_time, utc_time, timezone, epoch_seconds, date, time, year, weekday
- ntp_synchronized, ntp_enabled, can_ntp, local_rtc (derivati da timedatectl su Linux e W32Time su Windows)
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from typing import Any


def get_ntp_status_linux() -> dict[str, str]:
    """Interroga timedatectl su Linux per verificare la sincronizzazione NTP."""
    res = {
        "timezone": "",
        "ntp_synchronized": "unknown",
        "ntp_enabled": "unknown",
        "can_ntp": "unknown",
        "local_rtc": "unknown",
    }
    try:
        proc = subprocess.run(
            ["timedatectl", "show"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().lower()
                    if k == "Timezone":
                        res["timezone"] = line.split("=", 1)[1].strip()
                    elif k == "NTPSynchronized":
                        res["ntp_synchronized"] = v
                    elif k == "NTP":
                        res["ntp_enabled"] = v
                    elif k == "CanNTP":
                        res["can_ntp"] = v
                    elif k == "LocalRTC":
                        res["local_rtc"] = v
    except (OSError, subprocess.TimeoutExpired):
        pass
    return res


def get_ntp_status_windows() -> dict[str, str]:
    """Verifica lo stato W32Time su Windows."""
    res = {
        "timezone": "",
        "ntp_synchronized": "unknown",
        "ntp_enabled": "unknown",
        "can_ntp": "unknown",
        "local_rtc": "unknown",
    }
    try:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\W32Time\Parameters") as key:
                ntp_type, _ = winreg.QueryValueEx(key, "Type")
                if ntp_type == "NoSync":
                    res["ntp_enabled"] = "no"
                    res["ntp_synchronized"] = "no"
                else:
                    res["ntp_enabled"] = "yes"
                    res["can_ntp"] = "yes"
                    res["ntp_synchronized"] = "yes"
        except (OSError, PermissionError):
            pass
    except ImportError:
        pass
    return res


def get_agent_now_data() -> dict[str, Any]:
    """Genera il dizionario completo a 13 campi."""
    now_local = datetime.datetime.now().astimezone()
    now_utc = now_local.astimezone(datetime.timezone.utc)

    # Informazioni NTP e Timezone
    if sys.platform == "win32":
        ntp_info = get_ntp_status_windows()
    else:
        ntp_info = get_ntp_status_linux()

    tz_name = ntp_info.get("timezone") or now_local.tzname() or "UTC"

    # Formattazione ISO compatibile con date -Is
    local_iso = now_local.isoformat(timespec="seconds")
    utc_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # Formattazione data e ora
    local_date = now_local.strftime("%Y-%m-%d")
    local_time = now_local.strftime("%H:%M:%S%z")
    weekday = now_local.strftime("%A")

    return {
        "source": "system_clock",
        "local_time": local_iso,
        "utc_time": utc_iso,
        "timezone": tz_name,
        "epoch_seconds": int(now_local.timestamp()),
        "date": local_date,
        "time": local_time,
        "year": now_local.year,
        "weekday": weekday,
        "ntp_synchronized": ntp_info.get("ntp_synchronized", "unknown"),
        "ntp_enabled": ntp_info.get("ntp_enabled", "unknown"),
        "can_ntp": ntp_info.get("can_ntp", "unknown"),
        "local_rtc": ntp_info.get("local_rtc", "unknown"),
    }


def format_human(data: dict[str, Any]) -> str:
    return (
        f"Local: {data['local_time']}\n"
        f"UTC:   {data['utc_time']}\n"
        f"TZ:    {data['timezone']}\n"
        f"NTP:   synchronized={data['ntp_synchronized']} enabled={data['ntp_enabled']} can_ntp={data['can_ntp']} local_rtc={data['local_rtc']}"
    )


def format_shell(data: dict[str, Any]) -> str:
    lines = [
        f"AGENT_NOW_LOCAL_ISO='{data['local_time']}'",
        f"AGENT_NOW_UTC_ISO='{data['utc_time']}'",
        f"AGENT_NOW_TIMEZONE='{data['timezone']}'",
        f"AGENT_NOW_EPOCH_SECONDS={data['epoch_seconds']}",
        f"AGENT_NOW_DATE='{data['date']}'",
        f"AGENT_NOW_TIME='{data['time']}'",
        f"AGENT_NOW_YEAR={data['year']}",
        f"AGENT_NOW_WEEKDAY='{data['weekday']}'",
        f"AGENT_NOW_NTP_SYNCHRONIZED='{data['ntp_synchronized']}'",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic current date/time source for agents")
    parser.add_argument("--format", choices=["json", "human", "shell"], default="json")
    parser.add_argument("--json", action="store_true", help="Output in JSON")
    parser.add_argument("--human", action="store_true", help="Output human-readable")
    parser.add_argument("--shell", action="store_true", help="Output for shell eval")

    args = parser.parse_args(argv)

    fmt = args.format
    if args.json:
        fmt = "json"
    elif args.human:
        fmt = "human"
    elif args.shell:
        fmt = "shell"

    data = get_agent_now_data()

    if fmt == "json":
        print(json.dumps(data, indent=2))
    elif fmt == "human":
        print(format_human(data))
    elif fmt == "shell":
        print(format_shell(data))

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Apertura di una cartella nel file manager predefinito del sistema operativo."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_folder(path_str: str) -> int:
    """Apre la cartella specificata nel file manager del sistema."""
    path = Path(path_str)
    if not path.is_absolute():
        print("Errore: serve un percorso assoluto di una cartella esistente.", file=sys.stderr)
        return 2

    try:
        resolved = path.resolve(strict=True)
    except OSError:
        print("Errore: la cartella non esiste.", file=sys.stderr)
        return 2

    if not resolved.is_dir():
        print("Errore: il percorso non indica una cartella.", file=sys.stderr)
        return 2

    if sys.platform == "win32":
        try:
            os.startfile(str(resolved))
        except OSError as exc:
            print(f"Errore apertura cartella su Windows: {exc}", file=sys.stderr)
            return 1
    elif sys.platform == "darwin":
        cmd = ["open"]
        try:
            subprocess.run([*cmd, str(resolved)], check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            print(f"Errore apertura cartella su macOS: {exc}", file=sys.stderr)
            return 1
    else:
        # Linux (gio o xdg-open)
        cmd = ["gio", "open"] if shutil_which("gio") else (["xdg-open"] if shutil_which("xdg-open") else None)
        if not cmd:
            print("Errore: non trovo un comando per aprire il file manager (gio o xdg-open).", file=sys.stderr)
            return 127
        try:
            subprocess.run([*cmd, str(resolved)], check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            print(f"Errore apertura cartella: {exc}", file=sys.stderr)
            return 1

    print(f"Cartella aperta: {resolved}")
    return 0


def shutil_which(cmd: str) -> bool:
    import shutil
    return shutil.which(cmd) is not None


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print("Uso: agent-open-folder <percorso-assoluto-cartella>\n\nApre una cartella locale nel file manager predefinito.")
        return 0 if argv and argv[0] in ("-h", "--help") else 2

    return open_folder(argv[0])


if __name__ == "__main__":
    sys.exit(main())

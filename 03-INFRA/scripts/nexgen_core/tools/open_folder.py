#!/usr/bin/env python3
"""Opens a folder in the operating system's default file manager."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from nexgen_core.i18n import t


def open_folder(path_str: str) -> int:
    """Opens the given folder in the system's file manager."""
    path = Path(path_str)
    if not path.is_absolute():
        print(t("Error: an absolute path to an existing folder is required."), file=sys.stderr)
        return 2

    try:
        resolved = path.resolve(strict=True)
    except OSError:
        print(t("Error: the folder does not exist."), file=sys.stderr)
        return 2

    if not resolved.is_dir():
        print(t("Error: the path does not point to a folder."), file=sys.stderr)
        return 2

    if sys.platform == "win32":
        try:
            os.startfile(str(resolved))
        except OSError as exc:
            print(t("Error opening folder on Windows: {error}", error=exc), file=sys.stderr)
            return 1
    elif sys.platform == "darwin":
        cmd = ["open"]
        try:
            subprocess.run([*cmd, str(resolved)], check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            print(t("Error opening folder on macOS: {error}", error=exc), file=sys.stderr)
            return 1
    else:
        # Linux (gio or xdg-open)
        cmd = ["gio", "open"] if shutil_which("gio") else (["xdg-open"] if shutil_which("xdg-open") else None)
        if not cmd:
            print(t("Error: could not find a command to open the file manager (gio or xdg-open)."), file=sys.stderr)
            return 127
        try:
            subprocess.run([*cmd, str(resolved)], check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            print(t("Error opening folder: {error}", error=exc), file=sys.stderr)
            return 1

    print(t("Folder opened: {resolved}", resolved=resolved))
    return 0


def shutil_which(cmd: str) -> bool:
    import shutil
    return shutil.which(cmd) is not None


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print(t("Usage: agent-open-folder <absolute-folder-path>\n\nOpens a local folder in the default file manager."))
        return 0 if argv and argv[0] in ("-h", "--help") else 2

    return open_folder(argv[0])


if __name__ == "__main__":
    sys.exit(main())

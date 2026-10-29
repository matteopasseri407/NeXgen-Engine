"""What the Companion app's own log says about its node, read from the end.

One job: turn the tail of `%LOCALAPPDATA%\\OpenClawTray\\openclaw-tray.log` into the
two answers the rest of the engine asks of it -- did the app's node connect, and
did it fail for want of a node credential -- without ever opening the file whole.

The log is read from its end in one bounded read: the app writes a health
snapshot about once a minute, and the owner's log had reached several megabytes
with the node error written hours before its end. Nothing here writes, and a
missing or unreadable log is an unanswered question rather than a fault: it
says "no", which is the answer that changes nothing on the machine.
"""
from __future__ import annotations

import os
from pathlib import Path

#: The app's log, and the tail of it this module reads.
LOG_FILENAME = "openclaw-tray.log"
LOG_TAIL_BYTES = 1024 * 1024
#: What the app writes when it has node mode on but no credential to use it.
NODE_CREDENTIAL_ERROR = "no node credential available"
#: A line saying the app's node did connect, which makes an older error stale.
NODE_CONNECTED_WORDS = ("connected", "is ready")
#: The prefix of the line the app writes on every node state change, and the two
#: states worth telling apart. `Disconnected` contains `connected`, so the words
#: are matched whole instead of by substring.
NODE_STATUS_WORD = "node status"
NODE_STATUS_DOWN = "disconnected"
NODE_STATUS_UP = "connected"


def log_lines(path: Path, limit: int = LOG_TAIL_BYTES) -> list[str]:
    """The end of the app's log, in one bounded read: absent or unreadable means no lines."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            data = handle.read()
    except OSError:  # nexgen: allow reason="an app that wrote no log says nothing about its node"
        return []
    return data.decode("utf-8", errors="replace").splitlines()


def log_says_no_node_credential(log_path: Path, limit: int = LOG_TAIL_BYTES) -> bool:
    """Whether the app's latest node line is the missing-credential error.

    The two facts are taken in the order they were written: an error read after a
    line saying the node connected is stale and says nothing.
    """
    last_error = last_connected = -1
    for index, line in enumerate(log_lines(log_path, limit)):
        lowered = line.casefold()
        if NODE_CREDENTIAL_ERROR in lowered:
            last_error = index
        elif "node" in lowered and any(word in lowered for word in NODE_CONNECTED_WORDS):
            last_connected = index
    return last_error > last_connected


def log_says_node_connected(log_path: Path, limit: int = LOG_TAIL_BYTES) -> bool:
    """Whether the app's latest node status line says its node is connected.

    The app writes `Node status: <state>` and `Node status changed: <state>` on
    every transition, so the last of those lines is the state it is in now. No
    such line in the tail is not a connected node: the caller reads it as "not
    yet", which is the answer that changes nothing on the machine.
    """
    latest = ""
    for line in log_lines(log_path, limit):
        if NODE_STATUS_WORD in line.casefold():
            latest = line.casefold()
    if not latest:
        return False
    return NODE_STATUS_DOWN not in latest and NODE_STATUS_UP in latest

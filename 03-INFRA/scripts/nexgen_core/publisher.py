#!/usr/bin/env python3
"""The publisher (vault-push): safe publication of changes for NeXgen Engine v2.

Contract:
1. Acquires the host-wide exclusive lock (shared with the Guard).
2. Runs commit and push as a single atomic operation.
3. If the remote has moved ahead, attempts a clean rebase; on conflict it stops without destroying data.
4. Syncs the configured mirrors on a best-effort basis.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.git_ops import (
    get_current_branch,
    publish_changes,
    resolve_remotes,
)
from nexgen_core.lock import HostLock
from nexgen_core.paths import resolve_vault_data


class Publisher:
    """Publication manager for vault-push."""

    def __init__(self, vault_data: Path | None = None) -> None:
        _v = resolve_vault_data(override=vault_data)
        self.vault_data = _v

    def publish(
        self,
        message: str = "update: vault sync",
        files: list[str] | None = None,
        timeout: float = 30.0
    ) -> tuple[int, str]:
        """Runs the full publication flow under lock."""
        with HostLock(timeout=timeout, command_name="vault-push"):
            auth_remote, mirrors = resolve_remotes(self.vault_data)
            # Same precedence as the checks: whoever forces the branch via
            # the environment variable also forces it for publishing.
            branch = (
                os.environ.get("KNOWLEDGE_VAULT_BRANCH")
                or get_current_branch(self.vault_data)
                or "main"
            )

            success, msg = publish_changes(
                repo_dir=self.vault_data,
                branch=branch,
                remote=auth_remote,
                mirrors=mirrors,
                commit_msg=message,
                files_to_commit=files,
            )

            if success:
                return 0, msg
            return 1, msg


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the vault-push (v2) command."""
    parser = argparse.ArgumentParser(
        prog="vault-push",
        description="Publishes the Vault's commits to the authoritative remote",
    )
    parser.add_argument("-m", "--message", default="update: vault sync", help="Commit message")
    parser.add_argument("files", nargs="*", help="Specific files to publish")
    args = parser.parse_args(argv)

    pub = Publisher()
    code, msg = pub.publish(message=args.message, files=args.files or None)
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main())

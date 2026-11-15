"""This machine's own side of becoming a node: the connect handoff and its approvals.

One job: hand OpenClaw the two node-side facts it cannot guess, using only the
commands it documents. The join target goes into a private file that
`openclaw connect --target-file` reads and consumes, so a single-use code never
appears in a process list; and, only when the person asks for it by name, this
PC's exec approvals are aligned to the policy that never stops on one.

Nothing here waits for a connection. `openclaw connect --service` completes the
first authenticated connection before it installs the service ("OpenClaw
completes the first authenticated connection before installing the service",
docs.openclaw.ai/cli/connect) and exits non-zero when it cannot, so a successful
exit is the proof that the node is attached and a poll loop would only be a
second, weaker opinion about the same fact.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.openclaw.node_approvals import NEVER_PROMPT_BODY
from nexgen_core.openclaw.node_cli import SERVICE_TIMEOUT_S, Run, run_command
from nexgen_core.openclaw_service import RunResult

#: The one exec policy this module applies, when a person asks for it by name.
FULL_EXEC_POLICY = "full"


def _default_input_runner(cmd: list[str], stdin_text: str) -> RunResult:
    """The same run, with a body on standard input: `approvals set --stdin` takes its JSON that way."""
    res = subprocess.run(
        cmd, input=stdin_text, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    return RunResult(res.returncode, res.stdout, res.stderr)


def apply_exec_policy(
    runner: Callable[[list[str], str], RunResult] | None = None,
    out: Callable[[str], None] = print,
) -> bool:
    """Aligns this node's own exec approvals with the policy that never stops on one.

    A node runs the gateway's commands on a PC where nobody is sitting: a prompt
    raised there is answered by the fallback, and the documented default is `deny`,
    which the caller sees as `exec denied: request-failed`. The body is the one
    OpenClaw documents for exactly this host, and it is written locally on purpose:
    these approvals belong to this machine, not to the gateway's configuration.
    `openclaw exec-policy preset` would move the same values but writes the local
    `tools.exec` config with them, and the node's requested policy is not this
    module's to decide.

    False means nothing was changed, and the sentence names the command to run by hand.
    """
    run_fn = runner or _default_input_runner
    out(t("Aligning this node's exec approvals: full security, no prompt, fallback full..."))
    res = run_fn(["openclaw", "approvals", "set", "--stdin"], NEVER_PROMPT_BODY + "\n")
    if res.returncode != 0:
        out(t("This node's exec approvals were left as they were (exit code {code}). Run 'openclaw approvals "
              "set --stdin' here with this body: {body}", code=res.returncode, body=NEVER_PROMPT_BODY))
        return False
    out(t("This node no longer stops on an exec approval: its commands run without waiting for somebody."))
    return True


def connect_node_service(
    target: str,
    home: Path | None = None,
    runner: Run | None = None,
    out: Callable[[str], None] = print,
) -> bool:
    """Connects this machine as a node service, with the join target in a private file.

    The file is the handoff OpenClaw documents: it is read once, removed only after
    a non-empty read, and never passed as an argument, so a single-use join code
    stays out of the process list and out of this command's output.
    """
    run_fn = runner or run_command
    target_file = Path(tempfile.gettempdir()) / f"openclaw-connect-{os.getpid()}-{int(time.time())}.tmp"

    try:
        atomic_write_text(target_file, target.strip(), preserve_mode=False)

        out(t("Connecting node to gateway via 'openclaw connect --service --session-host' (protected target file)..."))
        cmd = ["openclaw", "connect", "--service", "--session-host", "--target-file", str(target_file)]
        res = run_fn(cmd, SERVICE_TIMEOUT_S)
        if res.returncode != 0:
            out(t("Node connect failed (exit code {code}).", code=res.returncode))
            return False
        out(t("Node connect command completed."))
        return True
    finally:
        target_file.unlink(missing_ok=True)

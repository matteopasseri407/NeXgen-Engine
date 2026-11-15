"""Does the deployed half of this installation run the same engine commit as the checkout?

One job: turn the three commit identities into the doctor's one line,
`deploy.alignment`. It is silent on a machine that runs nothing under
`03-INFRA/deploy/`, because "you have no server" is not a finding.

The gap it exists for: on a Cloud-Server install the engine on the VPS updates
itself on a timer, while the containers under `03-INFRA/deploy/` keep running
the code they were built from. Before this check nothing compared them, so a
server could serve a months-old write door next to a current engine and no
report said so.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.deploy import alignment
from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Severity

CHECK_ID = "deploy.alignment"

#: The one command that closes the gap, on the machine that runs the stack.
REMEDY = "nexgen deploy vault-mcp"


def check_deploy_alignment(
    home: Path, vault_data: Path, engine_root: Path, *, mcp_address: str | None = None,
) -> CheckOutcome | None:
    """The alignment line, or None on a machine with no deployed stack to align."""
    if not alignment.deploy_matters(home=home, vault_data=vault_data):
        return None
    state = alignment.measure(engine_root=engine_root, vault_data=vault_data, mcp_address=mcp_address)
    if state.engine is None:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.UNDETERMINED,
            message=t("This engine has no commit to compare: the checkout is not a git repository."),
        )
    if not state.answered:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.UNDETERMINED,
            message=t("The deployed vault-mcp did not answer on its health route, so its engine commit was not read ({detail}).",
                      detail=state.detail or t("no answer")),
            action=t("Nothing to do if the server is meant to be down. Otherwise check that the stack runs and the tunnel is open."),
        )
    if state.deployed is None:
        # An image built before images carried their commit: every server that updates
        # to this release from an older updater has one, and that updater does not
        # rebuild it. A failure here rolled the whole update back; it is a step to take.
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=t("The deployed vault-mcp does not say which engine commit it was built from, so this installation "
                      "cannot prove the server half is current."),
            action=t("On the server run '{command}': it rebuilds the service and stamps the commit it built.",
                     command=REMEDY),
        )
    if state.aligned:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.OK,
            message=t("Engine, Vault pin and deployed vault-mcp are on {commit}", commit=state.engine),
        )
    if state.deployed != state.engine:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.BROKEN,
            message=t("The deployed vault-mcp was built from {deployed}, this engine is {engine}: the server half is behind.",
                      deployed=state.deployed, engine=state.engine),
            action=t("On the server run '{command}': it rebuilds that one service, probes it, and puts the previous image "
                     "back if the probe fails. An update on a server now does this by itself.",
                     command=REMEDY),
        )
    if state.pin is not None and state.pin != state.engine:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=t("The Vault pins {pin} but this engine is {engine}: the other machines catch up on their next update.",
                      pin=state.pin, engine=state.engine),
            action=t("Nothing to do here: run 'nexgen update' on the machines that are behind."),
        )
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.OK,
        message=t("Engine and deployed vault-mcp are on {commit}", commit=state.engine),
    )

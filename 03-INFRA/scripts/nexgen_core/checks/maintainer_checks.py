"""The doctor's one line about the maintainer registration.

The maintainer tools refuse on an installation that is not registered, and that is the normal
state of every installation a person uses: there the check is green and says nothing worth
acting on. What it cannot stay quiet about is a machine that asked for the tools and cannot use
them - the flag set and the signing key half missing - because every gated command would then
refuse with nothing in the report to explain it.

One job: turn a `MaintainerStatus` into one doctor outcome.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core import maintainer
from nexgen_core.i18n import t
from nexgen_core.paths import remotes_config
from nexgen_core.report import CheckOutcome, Severity

CHECK_ID = "maintainer.registration"


def check_maintainer_registration(vault_data: Path, engine_root: Path) -> CheckOutcome:
    """Green on a normal installation and on a working maintainer, a warning on half a registration.

    The flag is one file read and decides whether the rest is worth asking: a machine that never
    asked for the tools is not asked for a signing key, a git call or a gpg call here.
    """
    config = remotes_config(vault_data)
    flag, flag_reason = maintainer.read_flag(config)
    if not flag:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.OK,
            message=t("Not a maintainer machine: the publishing tools are hidden here"),
        )
    status = maintainer.is_maintainer(config=config, anchor_dir=maintainer.anchor_beside(engine_root))
    if status.ok:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.OK,
            message=t("Registered as maintainer, and {reason}", reason=status.key_reason),
        )
    missing = status.key_reason if not status.key else flag_reason
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.WARN,
        message=t("Registered as maintainer but half of it is missing: {reason}", reason=missing),
        action=t("Run `nexgen maintainer status`: it names the missing half and how to supply it"),
    )

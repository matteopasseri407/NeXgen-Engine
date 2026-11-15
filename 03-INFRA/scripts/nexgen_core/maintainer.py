"""Whether this installation may run the maintainer tools, and what is missing when it may not.

The maintainer tools are the ones that publish the engine instead of using it: `nexgen release`,
the leak gate of the engine's own repository, the discipline hooks of that repository, and any
private tool the engine launches. They are switched on by two facts, and neither of them ships
in the public tree:

- ``maintainer: true`` in this installation's private config: the same ``sync/remotes.yaml``
  the update-channel gate already reads from the Vault. A normal installation does not carry it.
- a git signing key this machine would sign with, listed in the trust anchor the installed
  engine already pins (``trust/release-signers.txt``). The flag alone authorises nothing: it
  only says that somebody asked for the tools.

One job: answer that question, and hand back the one sentence that refuses a tool.

The lock is not here. GitHub's permissions are: ``main`` is protected, releases are signed and
the owner is the only collaborator, so a machine that set the flag without a pinned key still
cannot push, tag or release. This module keeps the tools out of the way of an installation that
has no use for them; it is not a security boundary and must not be described as one.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.paths import ENGINE_SUBDIR, is_inside, remotes_config, resolve_engine_root, resolve_vault_data
from nexgen_core.release_trust import (
    SIGNERS_FILE,
    TRUST_RELPATH,
    TrustAnchor,
    TrustAnchorError,
    load_trust_anchor,
)

#: The key that asks for the tools, in the private config: `maintainer: true`.
FLAG_FIELD = "maintainer"

#: The one refusal a gated command prints: one sentence, one action.
NOT_MAINTAINER = (
    "this is a maintainer tool; this installation is not registered as maintainer: "
    "see `nexgen maintainer status`"
)

GIT_TIMEOUT_SECONDS = 15.0
GPG_TIMEOUT_SECONDS = 10.0
KEYGEN_TIMEOUT_SECONDS = 10.0

_HEX_FINGERPRINT = re.compile(r"^[0-9A-F]{8,40}$")
_SSH_SHAPES = ("ssh-", "ecdsa-sha2-", "sk-ssh-", "sk-ecdsa-")
_OPENSSH_PRIVATE = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"


@dataclass(frozen=True)
class MaintainerStatus:
    """Both halves, each with the sentence that explains it: this is what a doctor line shows."""

    flag: bool
    flag_reason: str
    key: bool
    key_reason: str
    signing_key: str = ""

    @property
    def ok(self) -> bool:
        return self.flag and self.key

    @property
    def reasons(self) -> tuple[str, ...]:
        return (self.flag_reason, self.key_reason)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "flag": self.flag,
            "key": self.key,
            "signing_key": self.signing_key,
            "reasons": list(self.reasons),
        }


def config_path(home: Path | None = None) -> Path:
    """The private config this installation reads: the one the channel gate reads too."""
    return remotes_config(resolve_vault_data(home))


def read_flag(path: Path) -> tuple[bool, str]:
    """Whether the private config says ``maintainer: true``, with the sentence that says why.

    A file that cannot be parsed is not an error here: the answer to "may these tools run"
    is no, and the reason names the file so the person knows where to look.
    """
    from nexgen_core.cli.remotes_file import RemotesFileError, read_remotes_file

    try:
        data = read_remotes_file(path)
    except RemotesFileError as exc:
        return False, f"the private config could not be read ({path}): {exc}"
    if data.get(FLAG_FIELD) is True:
        return True, f"the private config says {FLAG_FIELD}: true ({path})"
    return False, f"the private config does not say {FLAG_FIELD}: true ({path})"


def anchor_beside(engine_root: Path) -> Path:
    """The trust anchor belonging to a given engine root.

    Two layouts reach here. A clone resolves the `03-INFRA` folder, and the anchor sits beside it
    at `TRUST_RELPATH`. A wheel ships the layer itself under `nexgen_core/_engine`, so the anchor
    is inside it, one level below the `03-INFRA` the path is written for.
    """
    root = Path(engine_root)
    if root.name == ENGINE_SUBDIR:
        return root.parent / TRUST_RELPATH
    return root / TRUST_RELPATH.relative_to(ENGINE_SUBDIR)


def trust_dir() -> Path:
    """The anchor this installation verifies releases against: its own, never a release's."""
    return anchor_beside(resolve_engine_root())


def _anchor(path: Path) -> tuple[TrustAnchor | None, str]:
    try:
        return load_trust_anchor(path), ""
    except TrustAnchorError as exc:
        return None, str(exc)


def _git(repo: Path | None, *args: str) -> str:
    """A git answer, or "" when git cannot give one: a status never crashes on a missing repo."""
    argv = ["git"] + (["-C", str(repo)] if repo is not None else []) + list(args)
    done = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=GIT_TIMEOUT_SECONDS, check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def _default_gpg_fingerprint() -> str:
    """The key gpg would sign with unasked: the first secret key of this machine's keyring."""
    gpg = shutil.which("gpg") or shutil.which("gpg2")
    if not gpg:
        return ""
    done = subprocess.run(
        [gpg, "--list-secret-keys", "--with-colons"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=GPG_TIMEOUT_SECONDS, check=False,
    )
    if done.returncode != 0:
        return ""
    for line in done.stdout.splitlines():
        fields = line.split(":")
        if fields[0] == "fpr" and len(fields) > 9 and fields[9]:
            return fields[9]
    return ""


def configured_signing_key(repo: Path | None = None) -> str:
    """What this machine's git would sign with: ``user.signingkey``, else gpg's default key.

    An SSH setup names the public key in ``user.signingkey``, so the value is handed back as
    git wrote it and interpreted by :func:`matches_signer`. When nothing is configured and git
    signs with OpenPGP, the keyring's default secret key is the key a tag would carry.
    """
    configured = _git(repo, "config", "--get", "user.signingkey")
    if configured:
        return configured
    if _git(repo, "config", "--get", "gpg.format").strip().lower() == "ssh":
        return ""
    return _default_gpg_fingerprint()


def _public_key_text(value: str) -> str:
    """The public key a ``user.signingkey`` points at, when the value is a file path.

    An SSH signing setup usually names the *private* key and lets git derive the public one,
    which is the value the anchor pins: `ssh-keygen -y` is what git calls for the same reason.
    """
    if not value or any(shape in value for shape in _SSH_SHAPES):
        return value
    path = Path(value).expanduser()
    if not path.is_file():
        return value
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # nexgen: allow reason="a key file that cannot be read is no usable key: the refusal names the configured value instead of crashing the status"
        return ""
    return _derived_public_key(path) if _OPENSSH_PRIVATE in text else text


def _derived_public_key(path: Path) -> str:
    """The public half of an OpenSSH private key, which is the value a release anchor carries."""
    keygen = shutil.which("ssh-keygen")
    if not keygen:
        return ""
    done = subprocess.run(
        [keygen, "-y", "-f", str(path)], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=KEYGEN_TIMEOUT_SECONDS, check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def matches_signer(key: str, anchor: TrustAnchor) -> bool:
    """Whether the key a machine would sign with is one the installed tree pins.

    Three shapes reach this function, because git accepts all three: a fingerprint (a key
    id is the same value's suffix), an SSH public key or the file holding one, and the
    ``key::`` form an SSH signing setup may use.
    """
    value = key.strip()
    if not value:
        return False
    if value.startswith("key::"):
        return _matches_ssh(value.removeprefix("key::"), anchor)
    fingerprint = value.removeprefix("0x").replace(" ", "").upper()
    if _HEX_FINGERPRINT.fullmatch(fingerprint):
        return any(pinned.endswith(fingerprint) for pinned in anchor.openpgp)
    return _matches_ssh(_public_key_text(value), anchor)


def _matches_ssh(material: str, anchor: TrustAnchor) -> bool:
    return any(f"{kind} {body}" in material for kind, body in anchor.ssh)


def _short(value: str) -> str:
    """Enough of a key to recognise it in a report, never more than a person needs."""
    if len(value) <= 24 or "/" in value or "\\" in value:
        return value
    return value[:8] + "…"


def _default_key() -> str:
    """What this machine's git would sign with, read from the checkout the engine runs in."""
    return configured_signing_key(engine_checkout_root())


def is_maintainer(
    home: Path | None = None,
    *,
    config: Path | None = None,
    anchor_dir: Path | None = None,
    resolve_key: Callable[[], str] | None = None,
) -> MaintainerStatus:
    """Both halves, read from where they live: the flag and the key the machine would sign with."""
    flag, flag_reason = read_flag(Path(config) if config is not None else config_path(home))
    anchor, anchor_reason = _anchor(Path(anchor_dir) if anchor_dir is not None else trust_dir())
    key = (resolve_key if resolve_key is not None else _default_key)()
    if anchor is None:
        return MaintainerStatus(flag, flag_reason, False, anchor_reason, key)
    if not key:
        return MaintainerStatus(
            flag, flag_reason, False,
            "no git signing key is configured on this machine (git config user.signingkey, "
            "or the keyring's default secret key)", key,
        )
    if matches_signer(key, anchor):
        return MaintainerStatus(
            flag, flag_reason, True, f"the signing key {_short(key)} is a listed release signer", key,
        )
    return MaintainerStatus(
        flag, flag_reason, False,
        f"the signing key {_short(key)} is not in {SIGNERS_FILE} of the installed engine", key,
    )


def tools_available() -> bool:
    """The question `--help` asks: cheap, and the answer is no on an installation that never asked.

    The flag is one file read. Only a machine that carried it pays for the rest, which is a
    git call and, without a configured signing key, one gpg call: a command that prints help
    runs on every session start and must not spend more than that.
    """
    if not read_flag(config_path())[0]:
        return False
    return is_maintainer().ok


def refusal(home: Path | None = None) -> str | None:
    """The refusal for a tool that is gated on every machine, or None when it may run."""
    return None if is_maintainer(home).ok else t(NOT_MAINTAINER)


def engine_checkout_root() -> Path | None:
    """The checkout this engine's own code lives in, or None when it is installed as a package."""
    root = Path(__file__).resolve().parents[3]
    return root if (root / ".git").exists() else None


def _toplevel(path: Path) -> Path:
    shown = _git(Path(path), "rev-parse", "--show-toplevel")
    return Path(shown) if shown else Path(path)


def is_engine_checkout(repo: Path, *, checkout: Path | None = None) -> bool:
    """Whether ``repo`` is that checkout: the engine's own tree, never a project it was run in."""
    root = engine_checkout_root() if checkout is None else Path(checkout)
    if root is None:
        return False
    top = _toplevel(Path(repo))
    return is_inside(top, root) and is_inside(root, top)


def engine_repo_refusal(
    repo: Path,
    home: Path | None = None,
    *,
    checkout: Path | None = None,
    status: MaintainerStatus | None = None,
) -> str | None:
    """The refusal when ``repo`` is the engine's own tree and this machine is not a maintainer.

    The discipline workflow is a product feature in a person's own projects; only its use on
    the engine itself, where its hooks and gates are the publisher's, is a maintainer surface.
    """
    if not is_engine_checkout(repo, checkout=checkout):
        return None
    decided = is_maintainer(home) if status is None else status
    return None if decided.ok else t(NOT_MAINTAINER)

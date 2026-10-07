#!/usr/bin/env python3
"""Verifies a release tag against a trust anchor that ships inside the install.

The updater used to ask ``git log --format=%G?`` about the *commit* a tag
points at, using whatever keys happened to be in the machine's own keyring.
For a release that is a merge commit created by GitHub, that is GitHub's key
vouching for GitHub's merge: it says nothing about the maintainer. And an
unsigned or foreign-signed release only printed a warning, so the hourly
unattended self-upgrade would have installed it.

Here the question is the right one: is the *tag object* signed by a key the
installed release already trusts?

- The anchor (``trust/release-signers.txt`` plus the OpenPGP public keys) is
  read from the checkout that is being updated, never from the release under
  verification. A release cannot vouch for itself. Rotating a key therefore
  takes two releases: one that ships the new key, signed by an old key, then
  the first one signed by the new key.
- OpenPGP verification runs in a throwaway ``GNUPGHOME`` holding only the
  pinned keys, so it neither depends on nor changes the user's keyring.
  SSH verification uses a generated ``allowed_signers`` for the same reason.
- The verdict has four statuses. ``verified`` is the only one that proves
  anything. ``bad`` and ``untrusted`` are a signature that is wrong or from
  somebody else; ``unverifiable`` is the absence of evidence (no signature,
  no anchor, no ``gpg``). The caller decides what each one costs.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

TRUST_RELPATH = Path("03-INFRA") / "agent-universal-layer" / "trust"
SIGNERS_FILE = "release-signers.txt"
OPENPGP_KEYS_FILE = "release-signing-keys.asc"

VERIFIED = "verified"
UNVERIFIABLE = "unverifiable"
UNTRUSTED = "untrusted"
BAD = "bad"

#: The one principal name in the generated allowed_signers. Matching it in
#: ssh-keygen's output is what proves the signing key is one of ours.
SSH_PRINCIPAL = "nexgen-release"
VERIFY_TIMEOUT_SECONDS = 30.0

_FINGERPRINT = re.compile(r"^[0-9A-F]{40}$")
_SSH_KEY_TYPE = re.compile(
    r"^(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com"
    r"|sk-ecdsa-sha2-nistp256@openssh\.com)$"
)
_SSH_KEY_BODY = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_PGP_MARKER = "-----BEGIN PGP SIGNATURE-----"
_SSH_MARKER = "-----BEGIN SSH SIGNATURE-----"
_SSH_GOOD_ANCHORED = re.compile(
    rf'^Good "git" signature for {re.escape(SSH_PRINCIPAL)} with \S+ key (SHA256:\S+)', re.MULTILINE
)
_SSH_GOOD_UNKNOWN_KEY = re.compile(r'^Good "git" signature with \S+ key (SHA256:\S+)', re.MULTILINE)


class TrustAnchorError(ValueError):
    """The trust anchor in the installed tree is missing or malformed."""


@dataclass(frozen=True)
class TagVerdict:
    status: str
    detail: str
    signer: str = ""

    @property
    def ok(self) -> bool:
        return self.status == VERIFIED


@dataclass(frozen=True)
class TrustAnchor:
    openpgp: frozenset[str]
    ssh: tuple[tuple[str, str], ...]
    openpgp_keys: Path | None


def load_trust_anchor(trust_dir: Path) -> TrustAnchor:
    """Parses the signer list strictly: a line it cannot read is an error, not a skip.

    A line is shaped ``openpgp <40 hex fingerprint>`` or ``ssh <type> <base64>``.
    The shapes are checked tightly because the SSH entry is spliced into a
    generated ``allowed_signers`` line, where a stray token would be an option.
    """
    signers = trust_dir / SIGNERS_FILE
    try:
        text = signers.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise TrustAnchorError(f"no trust anchor in the installed tree ({signers} is missing)") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise TrustAnchorError(f"cannot read the trust anchor {signers}: {exc}") from exc
    openpgp: list[str] = []
    ssh: list[tuple[str, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        parts = raw.split("#", 1)[0].split()
        if not parts:
            continue
        if parts[0] == "openpgp" and len(parts) == 2 and _FINGERPRINT.match(parts[1].upper()):
            openpgp.append(parts[1].upper())
        elif parts[0] == "ssh" and len(parts) >= 3 and _SSH_KEY_TYPE.match(parts[1]) and _SSH_KEY_BODY.match(parts[2]):
            ssh.append((parts[1], parts[2]))
        else:
            raise TrustAnchorError(f"{SIGNERS_FILE} line {number} is not a valid signer entry")
    if not openpgp and not ssh:
        raise TrustAnchorError(f"{SIGNERS_FILE} lists no signing keys")
    keys_file = trust_dir / OPENPGP_KEYS_FILE
    if openpgp and not keys_file.is_file():
        raise TrustAnchorError(f"{SIGNERS_FILE} pins OpenPGP keys but {OPENPGP_KEYS_FILE} is missing")
    return TrustAnchor(frozenset(openpgp), tuple(ssh), keys_file if openpgp else None)


def classify_gpg_status(status: str, allowed: frozenset[str]) -> TagVerdict:
    """Maps gpg's machine-readable ``--status-fd`` lines to a verdict.

    Status lines are used rather than the exit code or the human text because
    they are the stable interface and are not translated.
    """
    events: dict[str, list[list[str]]] = {}
    for line in status.splitlines():
        if line.startswith("[GNUPG:] "):
            fields = line[len("[GNUPG:] "):].split()
            if fields:
                events.setdefault(fields[0], []).append(fields[1:])
    if "BADSIG" in events:
        return TagVerdict(BAD, "the signature does not match the tag: it was altered or forged")
    if "REVKEYSIG" in events:
        return TagVerdict(BAD, "the tag is signed by a key that has been revoked")
    if "GOODSIG" in events and "VALIDSIG" in events:
        # VALIDSIG ends with the primary key's fingerprint, which is what the
        # anchor pins, so a signing subkey is covered by its primary.
        primaries = [(f[9] if len(f) >= 10 else f[0]).upper() for f in events["VALIDSIG"] if f]
        for primary in primaries:
            if primary in allowed:
                return TagVerdict(VERIFIED, "good OpenPGP signature by a pinned release key", signer=primary)
        return TagVerdict(
            UNTRUSTED,
            f"valid signature, but key {primaries[0] if primaries else 'unknown'} is not a pinned release key",
        )
    if "EXPKEYSIG" in events or "EXPSIG" in events:
        return TagVerdict(
            UNVERIFIABLE,
            "the signing key or the signature is expired according to the installed trust anchor",
        )
    if "NO_PUBKEY" in events:
        key = events["NO_PUBKEY"][0][0] if events["NO_PUBKEY"][0] else "unknown"
        return TagVerdict(UNTRUSTED, f"signed by key {key}, which is not a pinned release key")
    if "ERRSIG" in events:
        fields = events["ERRSIG"][0]
        reason = fields[5] if len(fields) > 5 else "?"
        return TagVerdict(UNVERIFIABLE, f"gpg could not check the signature (reason code {reason})")
    first = next((ln.strip() for ln in status.splitlines() if ln.strip()), "no output")
    return TagVerdict(UNVERIFIABLE, f"gpg gave no signature verdict ({first[:120]})")


def classify_ssh_result(returncode: int, output: str) -> TagVerdict:
    """Maps ``git verify-tag`` output for an SSH-signed tag to a verdict."""
    anchored = _SSH_GOOD_ANCHORED.search(output)
    if returncode == 0 and anchored:
        return TagVerdict(VERIFIED, "good SSH signature by a pinned release key", signer=anchored.group(1))
    unknown = _SSH_GOOD_UNKNOWN_KEY.search(output)
    if unknown:
        return TagVerdict(
            UNTRUSTED, f"valid signature, but key {unknown.group(1)} is not a pinned release key"
        )
    if "incorrect signature" in output or "Could not verify signature" in output:
        return TagVerdict(BAD, "the signature does not match the tag: it was altered or forged")
    first = next((ln.strip() for ln in output.splitlines() if ln.strip()), "no output")
    return TagVerdict(UNVERIFIABLE, f"ssh-keygen gave no signature verdict ({first[:120]})")


def _exec(args: Sequence[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=VERIFY_TIMEOUT_SECONDS, check=False,
    )


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return _exec(["git", "-C", str(repo), *args], env=env)


def _hermetic_env(**extra: str) -> dict[str, str]:
    # Fixed locale: the SSH classifier reads text, and that text is not
    # promised to be stable across languages.
    return {**os.environ, "LC_ALL": "C", "LANGUAGE": "C", **extra}


#: gpg starts its agent with a unix socket inside GNUPGHOME, and a socket path is limited to about 108 bytes. Past that gpg
#: cannot start the agent and every verification would come back "unverifiable", which on an unattended update means the
#: release is refused for a reason that has nothing to do with it. A long TMPDIR (a container, a CI workspace, a test) does it.
_GNUPG_BASE_LIMIT = 48


def _gnupg_scratch_base() -> str:
    base = tempfile.gettempdir()
    if os.name != "nt" and len(base) > _GNUPG_BASE_LIMIT and os.path.isdir("/tmp") and os.access("/tmp", os.W_OK):
        return "/tmp"
    return base


def _verify_openpgp(repo: Path, ref: str, anchor: TrustAnchor) -> TagVerdict:
    gpg = shutil.which("gpg")
    if not gpg or anchor.openpgp_keys is None:
        return TagVerdict(UNVERIFIABLE, "gpg is not installed, so an OpenPGP signature cannot be checked")
    gpg_path = Path(gpg).as_posix()
    with tempfile.TemporaryDirectory(prefix="nexgen-trust-", dir=_gnupg_scratch_base(), ignore_cleanup_errors=True) as home:
        env = _hermetic_env(GNUPGHOME=home)
        try:
            imported = _exec(
                [gpg, "--homedir", home, "--batch", "--no-tty", "--quiet", "--import", str(anchor.openpgp_keys)],
                env=env,
            )
            if imported.returncode != 0:
                return TagVerdict(UNVERIFIABLE, "gpg could not import the pinned release keys")
            proc = _git(
                repo,
                "-c", "gpg.format=openpgp",
                "-c", f"gpg.program={gpg_path}",
                "-c", f"gpg.openpgp.program={gpg_path}",
                "-c", "gpg.minTrustLevel=undefined",
                "verify-tag", "--raw", ref,
                env=env,
            )
            return classify_gpg_status(f"{proc.stderr}\n{proc.stdout}", anchor.openpgp)
        finally:
            # Importing keys starts a keyboxd/gpg-agent for this home. On Linux
            # (gpg 2.4.8) it exits by itself once the directory is removed;
            # stopping it first means cleanup never depends on that, which is
            # not something checked on other platforms.
            gpgconf = shutil.which("gpgconf")
            if gpgconf:
                try:
                    _exec([gpgconf, "--homedir", home, "--kill", "all"], env=env)
                except (OSError, subprocess.SubprocessError):
                    pass


def _verify_ssh(repo: Path, ref: str, anchor: TrustAnchor) -> TagVerdict:
    if not shutil.which("ssh-keygen"):
        return TagVerdict(UNVERIFIABLE, "ssh-keygen is not installed, so an SSH signature cannot be checked")
    with tempfile.TemporaryDirectory(prefix="nexgen-trust-", ignore_cleanup_errors=True) as home:
        allowed = Path(home) / "allowed_signers"
        allowed.write_text(
            "".join(f'{SSH_PRINCIPAL} namespaces="git" {kind} {body}\n' for kind, body in anchor.ssh),
            encoding="utf-8",
        )
        proc = _git(
            repo,
            "-c", "gpg.format=ssh",
            "-c", f"gpg.ssh.allowedSignersFile={allowed.as_posix()}",
            "verify-tag", ref,
            env=_hermetic_env(),
        )
        return classify_ssh_result(proc.returncode, f"{proc.stderr}\n{proc.stdout}")


def verify_release_tag(repo: Path, tag: str, *, trust_dir: Path | None = None) -> TagVerdict:
    """Verifies ``tag`` against the anchor in ``trust_dir`` (default: ``repo``'s own working tree)."""
    ref = f"refs/tags/{tag}"
    try:
        kind = _git(repo, "cat-file", "-t", ref)
        if kind.returncode != 0:
            return TagVerdict(UNVERIFIABLE, f"{tag} is not a tag in this repository")
        if kind.stdout.strip() != "tag":
            return TagVerdict(UNVERIFIABLE, f"{tag} is a lightweight tag, so it carries no signature")
        body = _git(repo, "cat-file", "tag", ref).stdout
        positions = {s: body.find(m) for s, m in (("openpgp", _PGP_MARKER), ("ssh", _SSH_MARKER)) if m in body}
        if not positions:
            return TagVerdict(UNVERIFIABLE, f"{tag} is an annotated tag with no signature")
        # The earliest block is the one git itself treats as the signature.
        scheme = min(positions, key=lambda s: positions[s])
        try:
            anchor = load_trust_anchor(trust_dir if trust_dir is not None else repo / TRUST_RELPATH)
        except TrustAnchorError as exc:
            return TagVerdict(UNVERIFIABLE, str(exc))
        if scheme == "openpgp":
            if not anchor.openpgp:
                return TagVerdict(UNTRUSTED, "signed with OpenPGP, but the trust anchor pins no OpenPGP key")
            return _verify_openpgp(repo, ref, anchor)
        if not anchor.ssh:
            return TagVerdict(UNTRUSTED, "signed with SSH, but the trust anchor pins no SSH key")
        return _verify_ssh(repo, ref, anchor)
    except (OSError, subprocess.SubprocessError) as exc:
        return TagVerdict(UNVERIFIABLE, f"signature check could not run ({type(exc).__name__})")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release_trust",
        description="Check whether installed copies would accept a release tag. Run it from the "
        "checkout that clients already have, before publishing the release.",
    )
    parser.add_argument("tag", help="release tag, for example v2.3.12")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository holding the tag")
    parser.add_argument("--trust-dir", type=Path, help="trust anchor directory (default: the repo's own)")
    args = parser.parse_args(argv)
    verdict = verify_release_tag(args.repo, args.tag, trust_dir=args.trust_dir)
    signer = f" [{verdict.signer}]" if verdict.signer else ""
    print(f"{args.tag}: {verdict.status}{signer}: {verdict.detail}")
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    sys.exit(main())

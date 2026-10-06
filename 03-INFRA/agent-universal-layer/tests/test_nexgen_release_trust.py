"""The updater's release-signature policy, tested with real signatures.

The check these replace read ``%G?`` of the commit a tag points at, against
whatever keys the machine's keyring happened to hold, and only warned when the
answer was "unknown". For a release that is a merge commit made by GitHub, it
was GitHub's key vouching for GitHub. A mocked ``%G?`` could never have shown
that, so these tests generate real OpenPGP and SSH keys, sign real tags, and
check what a client with an *empty* keyring concludes.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from conftest import REAL_VAULT
from nexgen_core import release_trust as rt
from nexgen_core.release_trust import BAD, UNTRUSTED, UNVERIFIABLE, VERIFIED
from test_nexgen_update_command import _env, _load_updater, _write_release

GPG = shutil.which("gpg")
SSH_KEYGEN = shutil.which("ssh-keygen")

needs_gpg = pytest.mark.skipif(GPG is None, reason="gpg is not installed")


def _git_version() -> tuple[int, ...]:
    out = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True).stdout
    return tuple(int(p) for p in out.split()[2].split(".")[:2])


needs_ssh_signing = pytest.mark.skipif(
    SSH_KEYGEN is None or _git_version() < (2, 34), reason="needs ssh-keygen and git >= 2.34"
)


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base = {
        **os.environ,
        "GIT_AUTHOR_NAME": "trust test", "GIT_AUTHOR_EMAIL": "trust-test@example.com",
        "GIT_COMMITTER_NAME": "trust test", "GIT_COMMITTER_EMAIL": "trust-test@example.com",
        **(env or {}),
    }
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=base)


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("release fixture\n", encoding="utf-8")
    _git(path, "add", "README")
    _git(path, "commit", "-q", "-m", "init")
    return path


# --------------------------------------------------------------------------
# Real keys. Module-scoped: generating a key is the slow part.
# --------------------------------------------------------------------------

class Keyring:
    def __init__(self, home: Path):
        self.home = home
        self.env = {**os.environ, "GNUPGHOME": str(home)}
        self.fingerprints: dict[str, str] = {}

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([GPG, "--batch", "--pinentry-mode", "loopback", "--passphrase", "", *args],
                              check=True, capture_output=True, text=True, env=self.env)

    def generate(self, name: str) -> str:
        self.run("--quick-generate-key", f"{name} <{name}@example.com>", "ed25519", "sign", "never")
        listing = self.run("--list-keys", "--with-colons", f"{name}@example.com").stdout
        fpr = next(line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:"))
        self.fingerprints[name] = fpr
        return fpr

    def add_signing_subkey(self, fpr: str) -> str:
        self.run("--quick-add-key", fpr, "ed25519", "sign", "never")
        listing = self.run("--list-keys", "--with-colons", "--with-subkey-fingerprints", fpr).stdout
        lines = listing.splitlines()
        sub = next(i for i, ln in enumerate(lines) if ln.startswith("sub:"))
        return next(ln.split(":")[9] for ln in lines[sub:] if ln.startswith("fpr:"))

    def export_public(self, *fingerprints: str) -> str:
        return self.run("--armor", "--export-options", "export-minimal", "--export", *fingerprints).stdout

    def sign_tag(self, repo: Path, tag: str, signing_key: str) -> None:
        _git(repo, "-c", f"user.signingkey={signing_key}", "-c", "gpg.format=openpgp",
             "-c", f"gpg.program={Path(GPG).as_posix()}", "tag", "-s", "-m", f"Release {tag}", tag, env=self.env)


@pytest.fixture(scope="module")
def keyring(tmp_path_factory):
    if GPG is None:
        pytest.skip("gpg is not installed")
    home = tmp_path_factory.mktemp("gnupg")
    home.chmod(0o700)
    ring = Keyring(home)
    ring.generate("maintainer")
    ring.generate("stranger")
    yield ring
    gpgconf = shutil.which("gpgconf")
    if gpgconf:
        subprocess.run([gpgconf, "--homedir", str(home), "--kill", "all"], check=False, capture_output=True)


@pytest.fixture
def client_keyring(tmp_path, monkeypatch):
    """The keyring of a machine that has never met the maintainer: empty."""
    home = tmp_path / "client-gnupg"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("GNUPGHOME", str(home))
    return home


def _write_anchor(trust_dir: Path, *, ring: Keyring | None = None, openpgp: tuple[str, ...] = (),
                  ssh_pub: tuple[Path, ...] = ()) -> Path:
    trust_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"openpgp {fpr}" for fpr in openpgp]
    lines += [f"ssh {' '.join(pub.read_text(encoding='utf-8').split()[:2])}" for pub in ssh_pub]
    (trust_dir / rt.SIGNERS_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    if openpgp:
        assert ring is not None
        (trust_dir / rt.OPENPGP_KEYS_FILE).write_text(ring.export_public(*openpgp), encoding="utf-8")
    return trust_dir


def _tamper(repo: Path, tag: str, new_tag: str) -> None:
    """A tag object with its message altered and the signature block left intact."""
    body = _git(repo, "cat-file", "tag", tag).stdout
    assert "Release" in body
    forged = body.replace("Release", "Releas3", 1).encode("utf-8")
    sha = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "tag", "-w", "--stdin"],
                         input=forged, capture_output=True, check=True).stdout.decode().strip()
    _git(repo, "update-ref", f"refs/tags/{new_tag}", sha)


# --------------------------------------------------------------------------
# The anchor file format.
# --------------------------------------------------------------------------

FPR_A = "5D061CE9626C9CC8BD88761F4399A81E895EB96F"
FPR_B = "6CD192BE0A787F77867F3B9F911522B8F02FFA88"
SSH_BODY = "AAAAC3NzaC1lZDI1NTE5AAAAIJI0QgCVCvqEoZe3UvTAn9vBV3KN3O/8iNrH4hjVR537"


def _anchor_dir(tmp_path: Path, text: str, *, keys: bool = True) -> Path:
    d = tmp_path / "trust"
    d.mkdir()
    (d / rt.SIGNERS_FILE).write_text(text, encoding="utf-8")
    if keys:
        (d / rt.OPENPGP_KEYS_FILE).write_text("placeholder\n", encoding="utf-8")
    return d


def test_anchor_parses_comments_blank_lines_and_normalizes_case(tmp_path):
    d = _anchor_dir(tmp_path, f"# why\n\nopenpgp {FPR_A.lower()}  # primary\nopenpgp {FPR_B}\nssh ssh-ed25519 {SSH_BODY} laptop\n")
    anchor = rt.load_trust_anchor(d)
    assert anchor.openpgp == {FPR_A, FPR_B}
    assert anchor.ssh == (("ssh-ed25519", SSH_BODY),)
    assert anchor.openpgp_keys == d / rt.OPENPGP_KEYS_FILE


@pytest.mark.parametrize("line", [
    "gpg " + FPR_A,                                    # unknown kind
    "openpgp " + FPR_A[:-1],                           # short fingerprint
    "openpgp " + FPR_A[:20] + " " + FPR_A[20:],        # spaced fingerprint is two tokens
    "openpgp 4399A81E895EB96F",                        # a key id is not a fingerprint
    "ssh cert-authority " + SSH_BODY,                  # not a key type
    "ssh ssh-ed25519 AAAA\" evil=1",                   # option smuggling through the key body
    "openpgp",
])
def test_anchor_refuses_a_line_it_cannot_read_instead_of_skipping_it(tmp_path, line):
    with pytest.raises(rt.TrustAnchorError):
        rt.load_trust_anchor(_anchor_dir(tmp_path, line + "\n"))


def test_anchor_refuses_missing_empty_or_keyless_files(tmp_path):
    with pytest.raises(rt.TrustAnchorError, match="missing"):
        rt.load_trust_anchor(tmp_path / "nowhere")
    with pytest.raises(rt.TrustAnchorError, match="no signing keys"):
        rt.load_trust_anchor(_anchor_dir(tmp_path, "# only a comment\n"))
    other = tmp_path / "second"
    other.mkdir()
    with pytest.raises(rt.TrustAnchorError, match="release-signing-keys.asc"):
        rt.load_trust_anchor(_anchor_dir(other, f"openpgp {FPR_A}\n", keys=False))


# --------------------------------------------------------------------------
# Classifiers, fed with output captured from real gpg / git / ssh-keygen.
# --------------------------------------------------------------------------

def _status(*lines: str) -> str:
    return "\n".join(f"[GNUPG:] {ln}" for ln in lines)


GOOD = ("NEWSIG", f"KEY_CONSIDERED {FPR_A} 0", "GOODSIG 4399A81E895EB96F Maintainer <m@example.com>",
        f"VALIDSIG {FPR_A} 2026-10-04 9999999999 0 4 0 1 8 00 {FPR_A}", "TRUST_UNDEFINED 0 pgp")


def test_gpg_good_signature_by_pinned_key_verifies():
    verdict = rt.classify_gpg_status(_status(*GOOD), frozenset({FPR_A}))
    assert (verdict.status, verdict.signer) == (VERIFIED, FPR_A)


def test_gpg_signing_subkey_is_covered_by_its_pinned_primary():
    subkey = "AB" * 20
    status = _status("GOODSIG 1 x", f"VALIDSIG {subkey} 2026-10-04 1 0 4 0 1 8 00 {FPR_A}")
    assert rt.classify_gpg_status(status, frozenset({FPR_A})).status == VERIFIED
    # Pinning the subkey itself is not what the anchor means, and must not work by accident.
    assert rt.classify_gpg_status(status, frozenset({subkey})).status == UNTRUSTED


def test_gpg_valid_signature_by_unpinned_key_is_untrusted_not_verified():
    verdict = rt.classify_gpg_status(_status(*GOOD), frozenset({FPR_B}))
    assert verdict.status == UNTRUSTED and FPR_A in verdict.detail


@pytest.mark.parametrize(("lines", "expected"), [
    (("NEWSIG", f"ERRSIG 4399A81E895EB96F 1 8 00 9999999999 9 {FPR_A}", "NO_PUBKEY 4399A81E895EB96F"), UNTRUSTED),
    (("BADSIG 2891F2B0BCBE2461 lab <lab@example.com>",), BAD),
    (("REVKEYSIG 1 x", f"VALIDSIG {FPR_A} 1 1 0 4 0 1 8 00 {FPR_A}"), BAD),
    (("EXPKEYSIG 1 x", f"VALIDSIG {FPR_A} 1 1 0 4 0 1 8 00 {FPR_A}"), UNVERIFIABLE),
    (("EXPSIG 1 x",), UNVERIFIABLE),
    (("ERRSIG 1 1 8 00 1 4 " + FPR_A,), UNVERIFIABLE),
    ((), UNVERIFIABLE),
])
def test_gpg_status_table(lines, expected):
    assert rt.classify_gpg_status(_status(*lines), frozenset({FPR_A})).status == expected


def test_gpg_one_bad_signature_wins_over_a_good_one():
    status = _status(*GOOD, "BADSIG 1 x")
    assert rt.classify_gpg_status(status, frozenset({FPR_A})).status == BAD


def test_gpg_text_without_status_lines_proves_nothing():
    # A localized human message is not a verdict, whatever it says.
    text = 'gpg: Good signature from "Maintainer <m@example.com>"'
    assert rt.classify_gpg_status(text, frozenset({FPR_A})).status == UNVERIFIABLE


@pytest.mark.parametrize(("rc", "output", "expected"), [
    (0, 'Good "git" signature for nexgen-release with ED25519 key SHA256:zCkq\n', VERIFIED),
    (1, 'Good "git" signature with ED25519 key SHA256:zCkq\nNo principal matched.\n', UNTRUSTED),
    (1, "Could not verify signature.\nSignature verification failed: incorrect signature\n", BAD),
    (1, "error: ssh-keygen -Y find-principals/verify is needed\n", UNVERIFIABLE),
    # A tag message can say anything; only a line starting the way ssh-keygen starts it counts.
    (1, 'subject: Good "git" signature for nexgen-release with ED25519 key SHA256:x\n', UNVERIFIABLE),
    (0, "", UNVERIFIABLE),
])
def test_ssh_output_table(rc, output, expected):
    assert rt.classify_ssh_result(rc, output).status == expected


# --------------------------------------------------------------------------
# Real OpenPGP signatures, verified by a client that has never met the key.
# --------------------------------------------------------------------------

@needs_gpg
def test_pinned_key_verifies_on_a_client_with_an_empty_keyring(tmp_path, keyring, client_keyring):
    repo = _make_repo(tmp_path / "repo")
    keyring.sign_tag(repo, "v1.0.0", keyring.fingerprints["maintainer"])
    anchor = _write_anchor(tmp_path / "anchor", ring=keyring, openpgp=(keyring.fingerprints["maintainer"],))

    verdict = rt.verify_release_tag(repo, "v1.0.0", trust_dir=anchor)

    assert verdict.status == VERIFIED and verdict.signer == keyring.fingerprints["maintainer"]
    # It borrowed a throwaway keyring: the machine's own was neither used nor changed.
    assert not any(client_keyring.glob("pubring*")) and not any(client_keyring.glob("public-keys*"))


@needs_gpg
def test_a_signing_subkey_verifies_through_its_pinned_primary(tmp_path, keyring, client_keyring):
    primary = keyring.generate("withsubkey")
    subkey = keyring.add_signing_subkey(primary)
    repo = _make_repo(tmp_path / "repo")
    keyring.sign_tag(repo, "v1.0.0", f"{subkey}!")
    anchor = _write_anchor(tmp_path / "anchor", ring=keyring, openpgp=(primary,))

    verdict = rt.verify_release_tag(repo, "v1.0.0", trust_dir=anchor)

    assert (verdict.status, verdict.signer) == (VERIFIED, primary)


@needs_gpg
def test_a_valid_signature_by_a_key_the_install_does_not_pin_is_untrusted(tmp_path, keyring, client_keyring):
    repo = _make_repo(tmp_path / "repo")
    keyring.sign_tag(repo, "v1.0.0", keyring.fingerprints["stranger"])
    anchor = _write_anchor(tmp_path / "anchor", ring=keyring, openpgp=(keyring.fingerprints["maintainer"],))

    assert rt.verify_release_tag(repo, "v1.0.0", trust_dir=anchor).status == UNTRUSTED


@needs_gpg
def test_a_tag_altered_after_signing_is_bad(tmp_path, keyring, client_keyring):
    repo = _make_repo(tmp_path / "repo")
    keyring.sign_tag(repo, "v1.0.0", keyring.fingerprints["maintainer"])
    _tamper(repo, "v1.0.0", "v1.0.0-forged")
    anchor = _write_anchor(tmp_path / "anchor", ring=keyring, openpgp=(keyring.fingerprints["maintainer"],))

    assert rt.verify_release_tag(repo, "v1.0.0", trust_dir=anchor).status == VERIFIED
    assert rt.verify_release_tag(repo, "v1.0.0-forged", trust_dir=anchor).status == BAD


@needs_gpg
def test_the_anchor_comes_from_the_installed_tree_never_from_the_release(tmp_path, keyring, client_keyring):
    """The attack this design exists for. Whoever can push a tag can also ship a
    trust directory in the commit it points at; if the release were allowed to
    name its own signers, any signature at all would verify."""
    repo = _make_repo(tmp_path / "repo")
    trust = repo / rt.TRUST_RELPATH
    _write_anchor(trust, ring=keyring, openpgp=(keyring.fingerprints["maintainer"],))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "installed anchor")
    installed = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _write_anchor(trust, ring=keyring, openpgp=(keyring.fingerprints["stranger"],))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "release that pins its own signer")
    keyring.sign_tag(repo, "v2.0.0", keyring.fingerprints["stranger"])

    _git(repo, "checkout", "-q", installed)  # the machine still runs the old tree
    assert rt.verify_release_tag(repo, "v2.0.0").status == UNTRUSTED

    _git(repo, "checkout", "-q", "main")  # the same tag, judged by its own anchor, "verifies"
    assert rt.verify_release_tag(repo, "v2.0.0").status == VERIFIED


@needs_gpg
def test_unsigned_lightweight_and_anchorless_releases_are_unverifiable(tmp_path, keyring, client_keyring):
    repo = _make_repo(tmp_path / "repo")
    _git(repo, "tag", "-a", "-m", "no signature", "v1.0.0")
    _git(repo, "tag", "v1.0.1")
    keyring.sign_tag(repo, "v1.0.2", keyring.fingerprints["maintainer"])
    anchor = _write_anchor(tmp_path / "anchor", ring=keyring, openpgp=(keyring.fingerprints["maintainer"],))

    assert "no signature" in rt.verify_release_tag(repo, "v1.0.0", trust_dir=anchor).detail
    assert "lightweight" in rt.verify_release_tag(repo, "v1.0.1", trust_dir=anchor).detail
    assert rt.verify_release_tag(repo, "v9.9.9", trust_dir=anchor).status == UNVERIFIABLE
    # A good signature with nothing installed to judge it against is still unproven.
    assert rt.verify_release_tag(repo, "v1.0.2", trust_dir=tmp_path / "nothing").status == UNVERIFIABLE
    for tag in ("v1.0.0", "v1.0.1"):
        assert rt.verify_release_tag(repo, tag, trust_dir=anchor).status == UNVERIFIABLE


@needs_gpg
@pytest.mark.parametrize("outcome", [VERIFIED, UNTRUSTED])
def test_verification_removes_its_throwaway_keyring(tmp_path, keyring, client_keyring, monkeypatch, outcome):
    """Runs hourly, so whatever it creates must not accumulate: no leftover
    directory, and no gpg daemon still holding one. (A daemon outliving its
    directory has not been seen on Linux with gpg 2.4.8, where it exits on its
    own; the check is for platforms where that is not guaranteed.)"""
    scratch = tmp_path / "scratch-tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    repo = _make_repo(tmp_path / "repo")
    keyring.sign_tag(repo, "v1.0.0", keyring.fingerprints["stranger" if outcome == UNTRUSTED else "maintainer"])
    anchor = _write_anchor(tmp_path / "anchor", ring=keyring, openpgp=(keyring.fingerprints["maintainer"],))

    assert rt.verify_release_tag(repo, "v1.0.0", trust_dir=anchor).status == outcome

    assert list(scratch.iterdir()) == []
    ps = shutil.which("ps")
    if ps is not None and os.name != "nt":
        # Match on the process *name*: grepping whole command lines would also
        # hit the shell that launched pytest whenever its own text says so.
        processes = subprocess.run([ps, "-eo", "comm=,args="], capture_output=True, text=True).stdout.splitlines()
        assert [ln for ln in processes
                if ln.split(None, 1)[0] in {"gpg-agent", "keyboxd", "dirmngr"} and str(scratch) in ln] == []


@needs_gpg
def test_cli_exit_code_says_whether_installed_copies_would_accept_the_tag(tmp_path, keyring, client_keyring, capsys):
    repo = _make_repo(tmp_path / "repo")
    keyring.sign_tag(repo, "v1.0.0", keyring.fingerprints["stranger"])
    anchor = _write_anchor(tmp_path / "anchor", ring=keyring, openpgp=(keyring.fingerprints["maintainer"],))

    assert rt.main(["v1.0.0", "--repo", str(repo), "--trust-dir", str(anchor)]) == 1
    assert "untrusted" in capsys.readouterr().out

    _write_anchor(anchor, ring=keyring, openpgp=(keyring.fingerprints["stranger"],))
    assert rt.main(["v1.0.0", "--repo", str(repo), "--trust-dir", str(anchor)]) == 0


# --------------------------------------------------------------------------
# Real SSH signatures.
# --------------------------------------------------------------------------

def _ssh_key(path: Path) -> Path:
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(path), "-C", "test"], check=True)
    return path.with_suffix(".pub")


def _ssh_tag(repo: Path, tag: str, pub: Path) -> None:
    _git(repo, "-c", "gpg.format=ssh", "-c", f"user.signingkey={pub.as_posix()}", "tag", "-s", "-m", f"Release {tag}", tag)


@needs_ssh_signing
def test_ssh_signed_release_verifies_and_a_foreign_ssh_key_does_not(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    ours, theirs = _ssh_key(tmp_path / "ours"), _ssh_key(tmp_path / "theirs")
    _ssh_tag(repo, "v1.0.0", ours)
    _ssh_tag(repo, "v1.0.1", theirs)
    _tamper(repo, "v1.0.0", "v1.0.0-forged")
    anchor = _write_anchor(tmp_path / "anchor", ssh_pub=(ours,))

    good = rt.verify_release_tag(repo, "v1.0.0", trust_dir=anchor)
    assert good.status == VERIFIED and good.signer.startswith("SHA256:")
    assert rt.verify_release_tag(repo, "v1.0.1", trust_dir=anchor).status == UNTRUSTED
    assert rt.verify_release_tag(repo, "v1.0.0-forged", trust_dir=anchor).status == BAD


@needs_ssh_signing
def test_an_ssh_tag_is_not_judged_by_an_openpgp_only_anchor(tmp_path, keyring):
    repo = _make_repo(tmp_path / "repo")
    _ssh_tag(repo, "v1.0.0", _ssh_key(tmp_path / "k"))
    anchor = _write_anchor(tmp_path / "anchor", ring=keyring, openpgp=(keyring.fingerprints["maintainer"],))

    assert rt.verify_release_tag(repo, "v1.0.0", trust_dir=anchor).status == UNTRUSTED


# --------------------------------------------------------------------------
# The updater's policy, end to end, with real signatures.
# --------------------------------------------------------------------------

def _signed_upgrade(tmp_path: Path, ring: Keyring, *, signer: str | None, pin: str) -> tuple[Path, Path]:
    """A machine at 0.1.0 whose installed tree pins ``pin``, and an origin whose
    0.2.0 release is signed by ``signer`` (a name in ``ring``) or not at all."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _write_release(origin, "0.1.0")
    _write_anchor(origin / rt.TRUST_RELPATH, ring=ring, openpgp=(ring.fingerprints[pin],))
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "pin the release signer")
    engine = tmp_path / "engine"
    _git(tmp_path, "clone", "-q", str(origin), str(engine))
    _write_release(origin, "0.2.0", previous="0.1.0")
    if signer is not None:
        _git(origin, "tag", "-d", "v0.2.0")
        ring.sign_tag(origin, "v0.2.0", ring.fingerprints[signer])
    return origin, engine


def _no_prompt(_text: str) -> str:
    raise AssertionError("unattended mode must never ask for confirmation")


@needs_gpg
def test_confirmed_update_installs_a_release_signed_by_a_pinned_key(tmp_path, keyring, client_keyring, capsys):
    _origin, engine = _signed_upgrade(tmp_path, keyring, signer="maintainer", pin="maintainer")

    result = _load_updater().main(["--yes"], environ=_env(engine))

    assert result == 0
    assert "Release signature: verified" in capsys.readouterr().out
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0"


@needs_gpg
def test_unattended_refuses_an_unsigned_release_and_moves_nothing(tmp_path, keyring, client_keyring, capsys):
    _origin, engine = _signed_upgrade(tmp_path, keyring, signer=None, pin="maintainer")
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()
    updater = _load_updater()
    # Allow the jump past the ceiling so the signature is the only thing in the way.
    updater._assert_within_unattended_ceiling = lambda *_a: None

    result = updater.main(["--unattended"], environ=_env(engine), input_fn=_no_prompt)

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    error = capsys.readouterr().err
    assert "unattended update refuses v0.2.0" in error and "nexgen-update --target 0.2.0" in error


@needs_gpg
def test_unattended_installs_a_pinned_signed_release(tmp_path, keyring, client_keyring):
    _origin, engine = _signed_upgrade(tmp_path, keyring, signer="maintainer", pin="maintainer")
    updater = _load_updater()
    updater._assert_within_unattended_ceiling = lambda *_a: None

    result = updater.main(["--unattended"], environ=_env(engine), input_fn=_no_prompt)

    assert result == 0
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0"


@needs_gpg
@pytest.mark.parametrize("flags", [["--unattended"], ["--yes"], ["--check"]])
def test_a_release_signed_by_a_stranger_is_refused_in_every_mode(tmp_path, keyring, client_keyring, capsys, flags):
    _origin, engine = _signed_upgrade(tmp_path, keyring, signer="stranger", pin="maintainer")
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()
    updater = _load_updater()
    updater._assert_within_unattended_ceiling = lambda *_a: None

    result = updater.main(flags, environ=_env(engine), input_fn=_no_prompt)

    assert result == updater.EXIT_REFUSED
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "not signed by a trusted release key" in capsys.readouterr().err


@needs_gpg
def test_interactive_update_of_an_unsigned_release_still_goes_through_with_a_warning(
    tmp_path, keyring, client_keyring, capsys
):
    """Interactive behaviour is unchanged for releases nobody can verify: a person
    reads the warning and decides. Only a *wrong* signature is refused outright."""
    _origin, engine = _signed_upgrade(tmp_path, keyring, signer=None, pin="maintainer")

    result = _load_updater().main(["--yes"], environ=_env(engine))

    assert result == 0
    assert "could not be verified" in capsys.readouterr().err
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0"


# --------------------------------------------------------------------------
# The anchor this repository actually ships, against the releases it actually made.
# --------------------------------------------------------------------------

SHIPPED = REAL_VAULT / rt.TRUST_RELPATH


def _tag_exists(tag: str) -> bool:
    return subprocess.run(["git", "-C", str(REAL_VAULT), "rev-parse", "--verify", "-q", f"refs/tags/{tag}"],
                          capture_output=True).returncode == 0


def test_shipped_anchor_is_well_formed():
    anchor = rt.load_trust_anchor(SHIPPED)
    assert anchor.openpgp and anchor.ssh


@needs_gpg
def test_shipped_key_material_is_exactly_the_pinned_fingerprints(tmp_path):
    anchor = rt.load_trust_anchor(SHIPPED)
    listing = subprocess.run(
        [GPG, "--homedir", str(tmp_path), "--batch", "--show-keys", "--with-colons", str(anchor.openpgp_keys)],
        capture_output=True, text=True, check=True,
    ).stdout
    primaries, expect_primary = set(), False
    for line in listing.splitlines():
        kind = line.split(":")[0]
        if kind == "pub":
            expect_primary = True
        elif kind == "fpr" and expect_primary:
            primaries.add(line.split(":")[9])
            expect_primary = False
    assert primaries == set(anchor.openpgp)


@needs_gpg
@pytest.mark.parametrize("tag", ["v2.3.11", "v2.3.10"])  # one release per pinned OpenPGP key
def test_real_openpgp_releases_verify_against_the_shipped_anchor(tag, client_keyring):
    if not _tag_exists(tag):
        pytest.skip(f"{tag} is not in this checkout")
    verdict = rt.verify_release_tag(REAL_VAULT, tag)
    assert verdict.status == VERIFIED, verdict


@needs_ssh_signing
def test_real_ssh_signed_release_verifies_against_the_shipped_anchor():
    if not _tag_exists("v2.1.6"):
        pytest.skip("v2.1.6 is not in this checkout")
    verdict = rt.verify_release_tag(REAL_VAULT, "v2.1.6")
    assert verdict.status == VERIFIED, verdict

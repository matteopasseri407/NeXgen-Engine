"""Which NeXgen Engine a machine follows, and why a moving branch is allowed.

Three channels. `stable` is the default for everyone: released tags of `main`.
`nightly` follows the tip of `developer`, and a commit enters it only with two
proofs: a signature the installed tree already pins, and the CI gates the branch
itself requires green on that commit. `beta` follows the newest signed commit
the full nightly suite proved (the `full-suite` status on the commit). Neither
is open unless the installation opens it in its own private config.

One job: name the commit the chosen channel points at, and remember the choice.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.errors import NexgenError
from nexgen_core.release_trust import BAD, UNTRUSTED, TagVerdict, verify_signed_commit

#: The version shape this engine releases in, and the one place it is parsed.
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

#: The channels a machine can follow, in the order they appear in `--help`.
CHANNELS = ("stable", "nightly", "beta")
DEFAULT_CHANNEL = "stable"
#: This machine's remembered channel, written in the engine's state directory.
CHANNEL_FILE = "update-channel"
#: The key in this INSTALLATION's private config that opens the channels that
#: are not stable: `update: {allow_channels: [nightly, beta]}` in sync/remotes.yaml.
ALLOW_SECTION = "update"
ALLOW_FIELD = "allow_channels"
#: The channels an installation may unlock. stable is not a permission: it is
#: what every installation has.
UNLOCKABLE = tuple(channel for channel in CHANNELS if channel != DEFAULT_CHANNEL)

DEVELOPER_BRANCH = "developer"
#: The public repository whose CI proves a `nightly` or `beta` candidate.
REPO_SLUG = "matteopasseri407/NeXgen-Engine"
NIGHTLY_LIMIT = 12
API_TIMEOUT_SECONDS = 20.0
GIT_TIMEOUT_SECONDS = 120.0


class ChannelError(NexgenError, RuntimeError):
    """A channel cannot answer right now, and the reason is actionable."""


class ChannelRefused(ChannelError):
    """The channel answered, and the answer must not be installed (exit 70)."""


@dataclass(frozen=True)
class ChannelTarget:
    """The commit a dev channel points at, with the proof that selected it."""

    channel: str
    rev: str
    version: str
    label: str
    detail: str
    #: The signature verdict that admitted this commit. Carried along so the
    #: update never judges the same commit twice and gets two answers.
    verdict: TagVerdict


@dataclass(frozen=True)
class TargetPlan:
    """What a run installs: a released tag, or a proved commit on a dev channel."""

    channel: str
    ref: str  # what the merge takes: a tag on stable, a commit sha on the dev channels
    rev: str
    version: str
    label: str
    release: bool
    channel_target: ChannelTarget | None = None


def version_tuple(value: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(value.strip())
    if not match:
        raise ChannelError(f"invalid semantic version: {value!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def released_tags(engine_repo: Path) -> list[str]:
    """Every semantic-version tag that is both merged into origin/main and published on the remote."""
    fetched = _git(engine_repo, "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main", "--tags")
    if fetched.returncode != 0:
        raise ChannelError(f"cannot fetch origin/main: {(fetched.stderr or fetched.stdout).strip()}")
    remote_main = _git(engine_repo, "rev-parse", "--verify", "origin/main")
    if remote_main.returncode != 0:
        raise ChannelError("origin/main is missing; cannot prove which tags are public releases")
    remote_tags = _git(engine_repo, "ls-remote", "--tags", "--refs", "origin").stdout
    published = {
        ref.removeprefix("refs/tags/")
        for line in remote_tags.splitlines()
        if len((fields := line.split())) == 2
        for ref in [fields[1]]
        if ref.startswith("refs/tags/")
    }
    listed = _git(engine_repo, "tag", "--merged", "origin/main", "--sort=-version:refname")
    return [
        line.strip()
        for line in listed.stdout.splitlines()
        if SEMVER.fullmatch(line.strip()) and line.strip() in published
    ]


def commits_at(engine_repo: Path, rev: str) -> str:
    """The commits a dev target carries; there is no changelog section to read."""
    listed = _git(engine_repo, "log", "--oneline", "--no-merges", "-n", "30", rev)
    return listed.stdout.strip() or "(no commits to show)"


def tag_version(engine_repo: Path, tag: str) -> str:
    """The VERSION a release tag carries, checked against the tag's own name and tree."""
    expected = tag.removeprefix("v")
    release_version = _git(engine_repo, "show", f"{tag}:VERSION").stdout.strip()
    version_tuple(release_version)
    if release_version != expected:
        raise ChannelError(f"release {tag} contains VERSION={release_version!r}; expected {expected!r}")
    entry = _git(engine_repo, "cat-file", "-e", f"{tag}:03-INFRA/scripts/nexgen_core/cli/__init__.py")
    if entry.returncode:
        raise ChannelError(f"release {tag} is missing its command entry; installed engine unchanged")
    return release_version


def stable_target(engine_repo: Path, requested: str | None = None) -> TargetPlan:
    """The newest released tag merged into origin/main, or the one asked for by name."""
    tags = released_tags(engine_repo)
    if not tags:
        raise ChannelError("origin/main has no released semantic-version tags")
    if not requested:
        tag = tags[0]
    else:
        tag = requested if requested.startswith("v") else f"v{requested}"
        if tag not in tags:
            raise ChannelError(f"{tag} is not a released tag merged into origin/main")
    return TargetPlan(
        channel=DEFAULT_CHANNEL, ref=tag,
        rev=_git(engine_repo, "rev-parse", f"{tag}^{{commit}}").stdout.strip(),
        version=tag_version(engine_repo, tag), label=tag, release=True,
    )


def plan_target(
    engine_repo: Path,
    state_dir: Path,
    *,
    channel: str | None,
    requested: str | None = None,
    allowed: frozenset[str] | None = None,
    vault_data: Path | None = None,
) -> TargetPlan:
    """The one place that answers "what does this run install", for every channel."""
    chosen = resolve_channel(channel, state_dir, allowed=allowed, vault_data=vault_data)
    if chosen == DEFAULT_CHANNEL:
        return stable_target(engine_repo, requested)
    if requested:
        raise ChannelError("--target names a released tag; it cannot be combined with a moving channel")
    proved = nightly_target(engine_repo) if chosen == "nightly" else beta_target(engine_repo)
    return TargetPlan(
        channel=chosen, ref=proved.rev, rev=proved.rev, version=proved.version,
        label=proved.label, release=False, channel_target=proved,
    )


def read_channel(state_dir: Path) -> str:
    """The channel this machine remembers; anything unreadable is the default."""
    try:
        value = (state_dir / CHANNEL_FILE).read_text(encoding="utf-8").strip().lower()
    except OSError:
        return DEFAULT_CHANNEL
    return value if value in CHANNELS else DEFAULT_CHANNEL


def write_channel(state_dir: Path, channel: str) -> None:
    if channel not in CHANNELS:
        raise ChannelError(f"unknown channel {channel!r}; expected one of {', '.join(CHANNELS)}")
    from nexgen_core.files import atomic_write_text

    state_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(state_dir / CHANNEL_FILE, channel + "\n")


def resolve_channel(
    requested: str | None,
    state_dir: Path,
    *,
    allowed: frozenset[str] | None = None,
    vault_data: Path | None = None,
) -> str:
    """The channel this run follows: the flag if given, else the remembered one.

    The dev channels are closed unless THIS installation opened them in its
    private config (`update.allow_channels`). A remembered channel that is not
    unlocked is ignored and the machine is treated as stable, so an unlocked
    setting that is later removed closes the door again instead of failing
    every cycle; an explicitly requested channel that is not unlocked refuses
    with the reason, because a person typed it and deserves an answer.
    """
    unlocked = allowed_channels(vault_data) if allowed is None else allowed
    if requested is not None:
        if requested not in CHANNELS:
            raise ChannelError(f"unknown channel {requested!r}; expected one of {', '.join(CHANNELS)}")
        if requested != DEFAULT_CHANNEL and requested not in unlocked:
            raise ChannelRefused(
                f"the {requested} channel is not enabled on this installation; "
                f"add update.{ALLOW_FIELD} in the private config to open it"
            )
        return requested
    remembered = read_channel(state_dir)
    if remembered == DEFAULT_CHANNEL or remembered in unlocked:
        return remembered
    return DEFAULT_CHANNEL


def allowed_channels(vault_data: Path | None = None, *, config_path: Path | None = None) -> frozenset[str]:
    """The dev channels this installation unlocked, empty when nothing says otherwise.

    The setting is installation, not repository: it lives in the private config
    the engine reads from the vault, so the public tree carries this reader and
    a closed default and never a machine's name. Anything unreadable, or a
    value that is not a channel name, closes the door rather than opening it.
    """
    import yaml

    if config_path is not None:
        path = Path(config_path)
    else:
        from nexgen_core.paths import remotes_config

        path = remotes_config(vault_data)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:  # nexgen: allow reason="a config that does not parse cannot open a channel: the closed default is the answer"
        return frozenset()
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(data, dict):
        return frozenset()
    section = data.get(ALLOW_SECTION)
    if not isinstance(section, dict):
        return frozenset()
    requested = section.get(ALLOW_FIELD)
    if not isinstance(requested, list):
        return frozenset()
    return frozenset(str(entry).strip() for entry in requested if str(entry).strip() in UNLOCKABLE)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=GIT_TIMEOUT_SECONDS, check=False,
    )


def developer_tip(engine_repo: Path) -> str:
    """The remote tip of `developer`, fetched, never a stale local branch."""
    fetched = _git(engine_repo, "fetch", "origin", f"+refs/heads/{DEVELOPER_BRANCH}:refs/remotes/origin/{DEVELOPER_BRANCH}")
    if fetched.returncode != 0:
        raise ChannelError(f"cannot fetch origin/{DEVELOPER_BRANCH}: {(fetched.stderr or fetched.stdout).strip()}")
    tip = _git(engine_repo, "rev-parse", "--verify", f"origin/{DEVELOPER_BRANCH}^{{commit}}")
    if tip.returncode != 0:
        raise ChannelError(f"origin/{DEVELOPER_BRANCH} is missing on the remote")
    return tip.stdout.strip()


def _version_at(engine_repo: Path, rev: str) -> str:
    shown = _git(engine_repo, "show", f"{rev}:VERSION")
    version = shown.stdout.strip()
    if shown.returncode != 0 or not version:
        raise ChannelError(f"commit {rev[:9]} has no VERSION file: not a complete engine tree")
    return version


def _entry_present(engine_repo: Path, rev: str) -> bool:
    entry = _git(engine_repo, "cat-file", "-e", f"{rev}:03-INFRA/scripts/nexgen_core/cli/__init__.py")
    return entry.returncode == 0


def _signed_target(engine_repo: Path, rev: str, *, channel: str, detail: str, trust_dir: Path | None) -> ChannelTarget:
    if not _entry_present(engine_repo, rev):
        raise ChannelError(f"commit {rev[:9]} is missing the command entry; nothing was installed")
    version = _version_at(engine_repo, rev)
    verdict = verify_signed_commit(engine_repo, rev, trust_dir=trust_dir)
    if verdict.status in (BAD, UNTRUSTED):
        raise ChannelRefused(
            f"commit {rev[:9]} on {channel} is not signed by a trusted release key: {verdict.detail}. "
            "Nothing was installed: a channel that follows a moving branch never takes a commit the "
            "installed keys cannot vouch for."
        )
    if not verdict.ok:
        raise ChannelRefused(
            f"commit {rev[:9]} on {channel} is not installable: {verdict.detail}. "
            f"Only a signed commit enters this channel; waiting for {DEVELOPER_BRANCH} to advance is the safe move."
        )
    return ChannelTarget(
        channel=channel, rev=rev, version=version, label=f"{DEVELOPER_BRANCH}@{rev[:9]}",
        detail=f"{detail}; {verdict.detail}", verdict=verdict,
    )


def nightly_target(
    engine_repo: Path,
    *,
    trust_dir: Path | None = None,
    checks=None,
) -> ChannelTarget:
    """The tip of `developer`, if it is signed and the branch's own CI gates are green.

    Two proofs and no third: the pinned keys, and the gates developer requires of
    itself (the self-check leak scan, the Linux suite and the fast Windows gate).
    A commit that has not passed them is a commit nobody has judged yet.
    """
    tip = developer_tip(engine_repo)
    target = _signed_target(engine_repo, tip, channel="nightly", detail="tip of developer", trust_dir=trust_dir)
    missing = (checks_missing(REPO_SLUG, tip) if checks is None else checks(tip))
    if missing:
        raise ChannelRefused(
            f"commit {tip[:9]} on nightly has not passed the CI gates developer requires: "
            f"{', '.join(missing)}. Nothing was installed; the next push that goes green opens the channel."
        )
    return target


def beta_target(
    engine_repo: Path,
    *,
    lookup=None,
    trust_dir: Path | None = None,
) -> ChannelTarget:
    """The newest signed commit of `developer` the full nightly suite has proved.

    Walking the proved commits newest first means an unsigned or rewritten tip is
    skipped instead of blocking the channel: the machine lands on the newest
    commit that is both signed and proved. There is no other door: a commit the
    full suite did not run on carries no `full-suite` status, and no status means
    no candidate.
    """
    verified = (full_suite_shas() if lookup is None else lookup())
    if not verified:
        raise ChannelError(
            f"no commit of {DEVELOPER_BRANCH} carries a green '{FULL_SUITE_CONTEXT}' status; "
            "beta never installs a commit the full suite did not prove"
        )
    # The proved commits come from the remote history; fetch it before judging them.
    developer_tip(engine_repo)
    for rev in verified:
        present = _git(engine_repo, "cat-file", "-e", f"{rev}^{{commit}}")
        if present.returncode != 0:
            continue  # rewritten away, or older than this clone's history
        try:
            return _signed_target(
                engine_repo, rev, channel="beta",
                detail=f"{FULL_SUITE_CONTEXT} green on Windows and Linux", trust_dir=trust_dir,
            )
        except ChannelRefused:  # nexgen: allow reason="a candidate the pinned keys refuse is skipped for the next proved one, never installed"
            continue
    raise ChannelError(
        f"no commit carrying a green '{FULL_SUITE_CONTEXT}' status on {DEVELOPER_BRANCH} is signed by a "
        "pinned key; beta has no candidate"
    )


# --------------------------------------------------------------------------
# The public API of the proofs: a `full-suite` status for beta, the branch's
# own CI gates for nightly. Both are read from the commit, never from the run.
# --------------------------------------------------------------------------

#: The commit status the full-suite workflow publishes, after both legs, on the
#: commit of `developer` it tested (see .github/workflows/ci.yml).
FULL_SUITE_CONTEXT = "full-suite"

#: The CI jobs a `nightly` candidate must have passed, by check-run name.
#: `self-check` carries the leak scan, `engine-tests` is the Linux suite,
#: `quick-windows` is the fast Windows gate: the minimum that keeps a machine
#: from being handed a commit the branch itself would not merge.
REQUIRED_CHECKS = ("self-check", "engine-tests", "quick-windows")


def _parse_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ChannelError(f"the GitHub API answered with something that is not JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ChannelError("the GitHub API answered with an unexpected shape")
    return data


def _https_api(path: str) -> dict:
    request = urllib.request.Request(
        f"https://api.github.com/{path}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "nexgen-engine"},
    )
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as answer:
            return _parse_json(answer.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ChannelError(f"the GitHub API could not be reached anonymously ({exc})") from exc


def _api_get(path: str) -> dict:
    """`gh` when it is there (it carries the user's auth), the public API otherwise.

    A `gh` that is installed but not logged in must not hide the answer a
    public repository gives anyway, so its failure falls through.
    """
    gh = shutil.which("gh")
    if gh:
        done = subprocess.run(
            [gh, "api", path], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=API_TIMEOUT_SECONDS, check=False,
        )
        if done.returncode == 0:
            return _parse_json(done.stdout)
    return _https_api(path)


def full_suite_state(slug: str, sha: str) -> str:
    """The state the full nightly suite published for this commit: success, failure, pending, or ''.

    Only the `full-suite` context counts. GitHub's own "Verified" badge is never
    consulted anywhere in this module: a moving-branch commit is trusted because
    the install's pinned keys signed it, and proved because the suite that ran
    Windows and Linux published a green status on it.
    """
    payload = _api_get(f"repos/{slug}/commits/{sha}/status")
    statuses = payload.get("statuses")
    for status in statuses if isinstance(statuses, list) else []:
        if isinstance(status, dict) and status.get("context") == FULL_SUITE_CONTEXT:
            return str(status.get("state") or "")
    return ""


def check_run_conclusions(slug: str, sha: str) -> dict[str, str]:
    """Latest conclusion per check-run name on this commit ('' while it is still running)."""
    payload = _api_get(f"repos/{slug}/commits/{sha}/check-runs?per_page=100")
    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        raise ChannelError("the GitHub API answered without a check-run list")
    conclusions: dict[str, str] = {}
    for run in runs:  # newest first: the first entry per name is the run that counts
        if not isinstance(run, dict):
            continue
        name = str(run.get("name") or "")
        if name and name not in conclusions:
            conclusions[name] = str(run.get("conclusion") or "")
    return conclusions


def checks_missing(slug: str, sha: str, required: tuple[str, ...] = REQUIRED_CHECKS) -> list[str]:
    """Which required CI checks this commit has not passed, empty when it passed them all."""
    conclusions = check_run_conclusions(slug, sha)
    return [
        f"{name}={conclusions.get(name) or 'not finished'}"
        for name in required
        if conclusions.get(name) != "success"
    ]


def full_suite_shas(
    slug: str = REPO_SLUG,
    *,
    branch: str = DEVELOPER_BRANCH,
    limit: int = NIGHTLY_LIMIT,
) -> list[str]:
    """Commits of `branch` with a green `full-suite` status, newest first.

    The status, not the run: GitHub runs `schedule` only on the default branch,
    so reading the event would report `main` every night while the branch a
    machine follows stayed unproved. One status read per candidate commit, and
    the list is capped by `limit`: this runs from a machine's update cycle, and
    an API budget spent walking history is a budget spent for nothing.
    """
    listed = _api_get(f"repos/{slug}/commits?sha={branch}&per_page={limit}")
    commits = listed.get("commits")
    if not isinstance(commits, list):
        raise ChannelError("the GitHub API answered without a commit list")
    verified: list[str] = []
    for entry in commits:
        sha = entry.get("sha") if isinstance(entry, dict) else None
        if sha and full_suite_state(slug, str(sha)) == "success":
            verified.append(str(sha))
    return verified


def short_head(repo: Path) -> str:
    """The short commit this checkout is on, or '' when there is no repository to read."""
    shown = _git(repo, "rev-parse", "--short", "HEAD")
    return shown.stdout.strip() if shown.returncode == 0 else ""

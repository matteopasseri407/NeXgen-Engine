"""Which NeXgen Engine a machine follows, and why a dev target is allowed.

A stable machine follows released tags. A machine on `dev` follows the tip of
`developer`, and a machine on `dev-verified` follows the newest signed commit
that the nightly CI run proved. The two dev channels exist because a moving
branch is only installable when something outside the branch vouches for it:
a signature from a key the installed tree already pins, and, for
`dev-verified`, a schedule run of the CI workflow that finished green with the
Windows and the Linux leg inside it. No green nightly, no candidate, ever.

One job: name the commit the chosen channel points at, and remember the choice.
The updater owns the merge, the rollback and the doctor; this module owns the
answer and the reasons it can refuse.
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
CHANNELS = ("stable", "dev", "dev-verified")
DEFAULT_CHANNEL = "stable"
#: This machine's remembered channel, written in the engine's state directory.
CHANNEL_FILE = "update-channel"
#: The key in this INSTALLATION's private config that opens the dev channels:
#: `update: {allow_channels: [dev, dev-verified]}` in sync/remotes.yaml.
ALLOW_SECTION = "update"
ALLOW_FIELD = "allow_channels"
#: The channels an installation may unlock. stable is not a permission: it is
#: what every installation has.
UNLOCKABLE = tuple(channel for channel in CHANNELS if channel != DEFAULT_CHANNEL)

DEVELOPER_BRANCH = "developer"
#: The public repository whose Actions runs prove a dev-verified candidate.
REPO_SLUG = "matteopasseri407/NeXgen-Engine"
CI_WORKFLOW = "ci.yml"
NIGHTLY_LIMIT = 30
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
        raise ChannelError("--target names a released tag; it cannot be combined with a dev channel")
    proved = dev_target(engine_repo) if chosen == "dev" else dev_verified_target(engine_repo)
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
            "Nothing was installed: a dev channel never takes a commit the installed keys cannot vouch for."
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


def dev_target(engine_repo: Path, *, trust_dir: Path | None = None) -> ChannelTarget:
    """The tip of `developer`, if and only if the tip itself is signed."""
    tip = developer_tip(engine_repo)
    return _signed_target(engine_repo, tip, channel="dev", detail="tip of developer", trust_dir=trust_dir)


def dev_verified_target(
    engine_repo: Path,
    *,
    lookup=None,
    trust_dir: Path | None = None,
) -> ChannelTarget:
    """The newest signed commit of `developer` a green nightly actually ran on.

    Walking the green runs newest first means an unsigned or rewritten tip is
    skipped instead of blocking the channel: the machine lands on the newest
    commit that is both signed and proved.
    """
    green = (green_nightly_shas() if lookup is None else lookup())
    if not green:
        raise ChannelError(
            f"no schedule run of {CI_WORKFLOW} on {DEVELOPER_BRANCH} has finished green on Windows and Linux; "
            "dev-verified never installs a commit the nightly did not prove"
        )
    # The green commits come from the remote history; fetch it before judging them.
    developer_tip(engine_repo)
    for rev in green:
        present = _git(engine_repo, "cat-file", "-e", f"{rev}^{{commit}}")
        if present.returncode != 0:
            continue  # rewritten away, or older than this clone's history
        try:
            return _signed_target(
                engine_repo, rev, channel="dev-verified",
                detail="green nightly on Windows and Linux", trust_dir=trust_dir,
            )
        except ChannelRefused:  # nexgen: allow reason="a candidate the pinned keys refuse is skipped for the next green one, never installed"
            continue
    raise ChannelError(
        "no commit in the recent green nightlies of "
        f"{DEVELOPER_BRANCH} is signed by a pinned key; dev-verified has no candidate"
    )


# --------------------------------------------------------------------------
# The public API of the nightly proof: schedule runs, green, both platforms.
# --------------------------------------------------------------------------

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


#: Runner labels that mean "this job ran on Windows" / "... on Linux".
_WINDOWS_LABELS = ("windows",)
_LINUX_LABELS = ("ubuntu", "linux")


def _platforms(jobs_payload: dict) -> set[str]:
    """Which platforms the jobs of one run covered, counting only green jobs."""
    found: set[str] = set()
    for job in jobs_payload.get("jobs", []):
        if job.get("conclusion") != "success":
            continue
        labels = " ".join(str(label).lower() for label in job.get("labels", []))
        if any(token in labels for token in _WINDOWS_LABELS):
            found.add("windows")
        if any(token in labels for token in _LINUX_LABELS):
            found.add("linux")
    return found


def green_nightly_shas(
    slug: str = REPO_SLUG,
    *,
    branch: str = DEVELOPER_BRANCH,
    limit: int = NIGHTLY_LIMIT,
) -> list[str]:
    """Head commits of recent successful schedule runs, newest first.

    "Successful" is not enough on its own: a run that skipped the Windows leg
    would prove half of what dev-verified claims, so the jobs of each run are
    read back and only the runs that really covered Windows and Linux count.
    """
    runs = _api_get(
        f"repos/{slug}/actions/workflows/{CI_WORKFLOW}/runs"
        f"?branch={branch}&event=schedule&status=success&per_page={limit}"
    )
    shas: list[str] = []
    for run in runs.get("workflow_runs", []):
        if run.get("event") != "schedule" or run.get("conclusion") != "success":
            continue
        run_id = run.get("id")
        sha = run.get("head_sha")
        if not run_id or not sha:
            continue
        platforms = _platforms(_api_get(f"repos/{slug}/actions/runs/{run_id}/jobs?per_page=100"))
        if {"windows", "linux"} <= platforms:
            shas.append(str(sha))
    return shas


def version_footer(state_dir: Path | None = None) -> str:
    """What `nexgen --version` appends: the channel, and its commit on the dev ones.

    Never raises: a version banner is not a place where a missing repository
    or an unreadable state file can be allowed to break the command.
    """
    try:
        from nexgen_core.paths import resolve_state_dir

        state = Path(state_dir) if state_dir is not None else resolve_state_dir()
        channel = read_channel(state)
        if channel == DEFAULT_CHANNEL:
            return f" ({channel})"
        from nexgen_core.paths import resolve_engine_root

        repo = resolve_engine_root().parent
        short = _git(repo, "rev-parse", "--short", "HEAD")
        sha = short.stdout.strip() if short.returncode == 0 else "unknown"
        return f" ({channel} {sha})"
    except Exception:  # nexgen: allow reason="a version banner never fails a command: the caller gets the plain version instead"
        return ""

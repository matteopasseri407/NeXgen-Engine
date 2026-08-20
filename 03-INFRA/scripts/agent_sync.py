#!/usr/bin/env python3
"""agent_sync — unified cross-platform provisioner for the KnowledgeVault
agent-universal-layer (Linux + Windows, one implementation).

Replaces the parallel agent-sync.sh / agent-sync.ps1 scripts, which used to
carry the same logic written twice in two languages with two mental models
(the root cause of "the twins drift apart": symlink vs junction, npx vs
npx.cmd, a scheduler that only self-heals on one OS). agent-sync.sh and
agent-sync.ps1 now only launch this script; the CLI, exit codes and log
file are unchanged, so the systemd timer, the Windows scheduled task and the
B1 test suite see no difference.

Modes:
  pull     Pull the KnowledgeVault from the remote and run healthcheck. Does not rewrite CLI runtime files.
  guard    Recurring safe propagation: pull, regenerate CLI runtime files, run healthcheck. Does not push.
  apply    Manual provisioning. Installs the available base and classifies
           strict readiness as READY or PARTIAL.
  publish  Push already-committed local vault changes to the authoritative remote, then configured mirrors.
  preflight  Validate every configuration input used by apply. Does not regenerate runtime files.
  doctor   Run healthcheck/alerts only.
  bootstrap-alerts  Provision optional alert credentials and run healthcheck.
With no arguments: print help and change nothing. The recurring
timer/scheduled task uses: agent_sync.py guard
Never auto-commits content: whoever writes commits (agents or the user).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import glob
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

import yaml

# This script is often run directly from a user's data-root compatibility
# layout. Importing a validator must not create __pycache__ entries there,
# especially on help/error paths that promise zero mutation.
sys.dont_write_bytecode = True
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config_schema import (  # noqa: E402
    ANTIGRAVITY_POSTURE_RENDER,
    CODEX_POSTURE_RENDER,
    GUARDRAIL_SHELL_MATCHER,
    OPENCODE_POSTURE_RENDER,
    PERMISSION_POSTURES,
    PERMISSION_RENDERERS,
    VERIFIED_HOOK_EVENTS,
    ConfigValidationError,
    load_council_config,
    load_mcp_manifest,
    parse_jsonc,
    parse_toml,
    set_jsonc_top_level_value,
    set_toml_root_string,
    toml_reader_available,
    validate_claude_settings,
    validate_permissions_manifest,
)

IS_WINDOWS = platform.system() == "Windows"
HOST_MUTATIONS_DISABLED_ENV = "NEXGEN_DISABLE_HOST_MUTATIONS"
WINDOWS_CMD_ENV_LIMIT = 8191


def _host_mutations_disabled(env: "Env", operation: str) -> bool:
    """Keep tests and dry-run harnesses away from machine-wide Windows state.

    HOME/USERPROFILE overrides redirect normal file writes, but they do not
    virtualize HKCU or Task Scheduler. Every sandboxed integration process
    must therefore cross this explicit boundary before touching either one.
    """
    value = os.environ.get(HOST_MUTATIONS_DISABLED_ENV, "").strip().lower()
    if value not in {"1", "true", "yes", "on"}:
        return False
    env.log(f"host-mutations: {operation} skipped ({HOST_MUTATIONS_DISABLED_ENV}=1)")
    return True


def _opencode_config_path(home: Path) -> Path:
    """Return the config path used by the installed OpenCode generation."""
    xdg_dir = home / ".config" / "opencode"
    xdg_candidates = [xdg_dir / name for name in ("opencode.jsonc", "opencode.json", "config.json")]
    for candidate in xdg_candidates:
        if candidate.is_file():
            return candidate
    if not IS_WINDOWS:
        return xdg_candidates[0]
    appdata_dir = Path(os.environ.get("APPDATA") or (home / "AppData" / "Roaming")) / "opencode"
    appdata_candidates = [appdata_dir / name for name in ("opencode.jsonc", "opencode.json", "config.json")]
    # Current native OpenCode reports ~/.config/opencode via `opencode debug
    # paths` on Windows too. APPDATA remains a compatibility fallback only
    # for an existing older install; preferring it when both files existed
    # made NeXgen patch a config the CLI no longer read.
    for candidate in appdata_candidates:
        if candidate.is_file():
            return candidate
    return xdg_candidates[0]

HELP_TEXT = """agent_sync modes:
  pull     Pull the KnowledgeVault from the remote and run healthcheck. Does not rewrite CLI runtime files.
  guard    Recurring safe propagation: pull, regenerate CLI runtime files, run healthcheck. Does not push.
  apply    Manual provisioning: pull, apply, then classify strict readiness.
           A successful base install may finish PARTIAL while credentials or
           consumers are still missing. Add --require-ready to require a
           strict doctor result with FAIL=0.
  publish  Push already-committed local vault changes to the authoritative remote, then configured mirrors.
  preflight  Validate every configuration input used by apply. Does not regenerate runtime files.
  doctor   Run healthcheck/alerts only.
  bootstrap-alerts  Provision optional alert credentials and run healthcheck.
  config FIELD  Print resolved sync data. FIELD is authoritative_remote or mirrors.
  inventory  Read-only onboarding scan: MCP servers, skills, and bootstrap per CLI, canonical vs out-of-manifest. Foundation of the adopt/reset flow. No writes.
  vault-push -m MSG [file ...]  Commit (+ stage given files) and publish the
    vault's infra files to the authoritative remote, then its mirrors. See
    docs/sync-contract.md and vault-write-architecture.md.

Default without arguments: help only, no writes.
The recurring timer/scheduled task should use: agent_sync.py guard
Use --allow-offline only with a deliberate manual apply when the authoritative
remote is temporarily unreachable and the local tracked tree is known-good.
"""

MODES = {
    "pull":    dict(pull=True,  apply=False, push=False, creds=False, health=True),
    "guard":   dict(pull=True,  apply=True,  push=False, creds=False, health=True),
    "apply":   dict(pull=True,  apply=True,  push=False, creds=False, health=True),
    "publish": dict(pull=False, apply=False, push=True,  creds=False, health=False),
    "preflight": dict(pull=False, apply=False, push=False, creds=False, health=False),
    "doctor":  dict(pull=False, apply=False, push=False, creds=False, health=True),
    "bootstrap-alerts": dict(pull=False, apply=False, push=False, creds=True, health=True),
}


class RemoteConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RemoteConfig:
    authoritative_remote: str
    mirrors: tuple[str, ...]
    source: str


_REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _validate_remote_name(value: str, source: str) -> str:
    value = value.strip()
    if not value or not _REMOTE_NAME_RE.fullmatch(value):
        raise RemoteConfigError(
            f"remote config {source}: invalid Git remote name {value!r}"
        )
    return value


def _parse_mirrors(value: object, source: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = [item.strip() for item in value if item.strip()]
    else:
        raise RemoteConfigError(f"remote config {source}: mirrors must be a list of strings")
    return tuple(dict.fromkeys(_validate_remote_name(item, source) for item in items))


def _parse_mirrors_lenient(value: object, source: str) -> tuple[str, ...]:
    """Same shape as _parse_mirrors, for the KNOWLEDGE_VAULT_MIRRORS
    emergency/bootstrap override only: one malformed entry is skipped with
    a warning instead of failing the whole config load. The data-owned
    remotes.yaml path keeps using the strict _parse_mirrors above -- a typo
    there is a real config bug worth stopping on. An ad-hoc env var typed
    by hand during an actual emergency must never brick the authoritative
    push over one bad mirror name (old vault-push.sh's behavior, restored
    here)."""
    if value is None:
        return ()
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = [item.strip() for item in value if item.strip()]
    else:
        print(f"vault-push: {source} mirrors must be a list of strings; ignoring KNOWLEDGE_VAULT_MIRRORS", file=sys.stderr)
        return ()
    valid: list[str] = []
    for item in items:
        try:
            valid.append(_validate_remote_name(item, source))
        except RemoteConfigError as exc:
            print(f"vault-push: {exc} -- skipping this mirror", file=sys.stderr)
    return tuple(dict.fromkeys(valid))


def load_remote_config(*, home: Path | None = None, vault_data: Path | None = None) -> RemoteConfig:
    """Resolve the authoritative Vault remote before any runtime mutation.

    A complete environment override is the emergency/bootstrap lane. Normal
    operation reads one data-owned YAML file shared by sync, doctor and
    publish. Missing data keeps the portable product default, origin.
    """
    env_remote = os.environ.get("KNOWLEDGE_VAULT_REMOTE", "").strip()
    if env_remote:
        env_remote = _validate_remote_name(env_remote, "environment")
        mirrors = _parse_mirrors_lenient(os.environ.get("KNOWLEDGE_VAULT_MIRRORS"), "environment")
        mirrors = tuple(item for item in mirrors if item != env_remote)
        return RemoteConfig(env_remote, mirrors, "environment")

    home = home or Path.home()
    vault_data = vault_data or Path(os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH") or str(home / "KnowledgeVault"))
    path = Path(os.environ.get("AGENT_SYNC_REMOTES_FILE") or str(vault_data / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml"))
    if not path.exists():
        return RemoteConfig("origin", (), "default")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RemoteConfigError(f"remote config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RemoteConfigError(f"remote config {path}: root must be a mapping")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise RemoteConfigError(f"remote config {path}: schema_version must be 1")
    remote = data.get("authoritative_remote")
    if not isinstance(remote, str) or not remote.strip():
        raise RemoteConfigError(f"remote config {path}: authoritative_remote must be a non-empty string")
    remote = _validate_remote_name(remote, str(path))
    mirrors = tuple(item for item in _parse_mirrors(data.get("mirrors"), str(path)) if item != remote)
    return RemoteConfig(remote, mirrors, str(path))


class Env:
    """Resolves every path/env var once. Path.home() honors $HOME on POSIX
    and %USERPROFILE% on Windows, so the same code runs unmodified in the
    B1 sandbox tests on either OS (see tests/conftest.py)."""

    def __init__(self) -> None:
        self.home = Path.home()
        self.vault = Path(os.environ.get("KNOWLEDGE_VAULT_PATH") or str(self.home / "KnowledgeVault"))
        self.branch = os.environ.get("KNOWLEDGE_VAULT_BRANCH") or "main"
        # Engine/data separation (Vault 2.1, Strangler Fig): defaults reproduce
        # the historical single-tree layout exactly, zero breakage.
        self.vault_data = Path(os.environ.get("AGENT_VAULT_DATA") or str(self.vault))
        remote_config = load_remote_config(home=self.home, vault_data=self.vault_data)
        self.remote = remote_config.authoritative_remote
        self.mirrors = remote_config.mirrors
        self.remote_config_source = remote_config.source
        self.local_bin = self.home / ".local" / "bin"
        default_engine_root = self.vault / "03-INFRA"
        # AGENT_ENGINE_ROOT wins when set. Otherwise, fall back to where
        # ~/.local/bin/agent-sync ACTUALLY resolves right now, not silently
        # to the vault default: a bare 'agent-sync apply/guard' run (no env
        # var exported -- the normal way anyone would type it) must not
        # revert an already-live cutover. Confirmed live: without this,
        # utils()'s self-healing agent-now symlink flipped back to the
        # vault's (now-deleted) copy on the very first plain invocation
        # after the S3 cutover. engine-rollback.sh remains the one
        # intentional way back: it swaps the symlink first, which this
        # then reads as "already at the default".
        self.engine_root = Path(os.environ.get("AGENT_ENGINE_ROOT") or self._persisted_engine_root(default_engine_root) or str(default_engine_root))
        self.engine_scripts = self.engine_root / "scripts"
        self.ul = self.engine_root / "agent-universal-layer"
        # Instance data (the user's own AGENTS.md, host-specific files): ALWAYS
        # from vault_data, regardless of where the engine lives. The engine
        # only ships the generic/universal AGENTS.md template; the personal
        # instance is data, never something the engine repo should serve.
        self.instance_ul = self.vault_data / "03-INFRA" / "agent-universal-layer"
        # Vault-only infra scripts that never get published to the engine
        # repo (sync-vault-from-oracle.sh, vault-push.sh, ...): always
        # data-anchored, regardless of where the engine lives.
        self.vault_scripts = self.vault_data / "03-INFRA" / "scripts"
        # `skills` is the intentionally tiny discovery view. The complete
        # library lives next to it, outside eager runtime discovery roots.
        self.active_skills = self.home / ".agents" / "skills"
        self.skill_library = self.home / ".agents" / "skill-library"
        self.log_dir = self.home / ".local" / "state"
        self.log_path = self.log_dir / "agent-sync.log"
        self.lock_path = self.log_dir / "agent-sync.lock"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Skill roots may still be broken whole-root links from a previous
        # eager layout. `vault_skills()` normalizes them before creating
        # anything, so do not call mkdir here and turn that recoverable state
        # into a FileExistsError.

    def _persisted_engine_root(self, default_engine_root: Path) -> str | None:
        link = self.local_bin / "agent-sync"
        if not link.is_symlink():
            return None
        try:
            target = link.resolve()
            default_resolved = default_engine_root.resolve()
        except OSError:
            return None
        root = target.parent.parent          # .../<engine-root>/scripts/agent-sync.sh -> <engine-root>
        return None if root == default_resolved else str(root)

    def log(self, message: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{_iso_now()} {message}\n")


def _iso_now() -> str:
    """Matches `date -Is` (e.g. 2026-07-08T15:36:42+02:00): local time,
    second precision, colon-separated UTC offset."""
    import datetime
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _append_log(env: Env, *chunks: str) -> None:
    text = "".join(c for c in chunks if c)
    if not text:
        return
    with env.log_path.open("a", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")


# ── generic OS adapters (the only place OS differences are allowed) ─────────

_REPARSE_POINT = 0x0400


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    if not IS_WINDOWS:
        return False
    is_junction = getattr(path, "is_junction", None)
    try:
        if callable(is_junction) and is_junction():
            return True
        # Path.is_junction() is unavailable on older supported Python builds.
        # Junctions are still directory reparse points there, so inspect the
        # Windows lstat attribute directly instead of treating them as normal
        # directories and recursing into their target.
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        return bool(attributes & _REPARSE_POINT)
    except OSError:
        return False


def _is_broken_whole_root_link(path: Path) -> bool:
    """True only for a whole-root link in the state vault_skills() exists
    to repair: a real symlink, or a Windows junction whose target is gone.

    Not a blanket _is_link_like() call (2026-08-15 council, Opus 5): the
    generic reparse-point bit is shared by IO_REPARSE_TAG_MOUNT_POINT
    (junctions, but also volume mount points) and IO_REPARSE_TAG_CLOUD
    (OneDrive placeholder folders), so _is_link_like() is True for a skills
    root that is merely a cloud placeholder -- classifying it here would
    destroy a real directory via _remove_path(). The two questions that
    settle it:
    1. A BROKEN junction: is_junction() still answers True (the reparse
       tag is read via lstat, which never follows the target), and
       path.is_dir() is False (the target does not exist). Repairable.
    2. A working junction or a cloud placeholder: is_dir() is True (the
       target or the local folder exists). Not repairable, not ours to
       remove -- leave it alone, mkdir(exist_ok=True) is a no-op on it."""
    if path.is_symlink():
        return True
    if not IS_WINDOWS:
        return False
    try:
        return _is_link_like(path) and not path.is_dir()
    except OSError:
        return False


def _points_to(path: Path, target: Path) -> bool:
    try:
        return _is_link_like(path) and path.resolve() == target.resolve()
    except OSError:
        return False


def _same_file_content(left: Path, right: Path) -> bool:
    try:
        return left.is_file() and right.is_file() and left.read_bytes() == right.read_bytes()
    except OSError:
        return False


def _same_tree_content(src: Path, dst: Path) -> bool:
    if not src.is_dir() or not dst.is_dir():
        return False
    try:
        src_entries = {p.relative_to(src).as_posix(): p for p in src.rglob("*")}
        dst_entries = {p.relative_to(dst).as_posix(): p for p in dst.rglob("*")}
    except OSError:
        return False
    if set(src_entries) != set(dst_entries):
        return False
    for rel, sp in src_entries.items():
        dp = dst_entries[rel]
        if sp.is_symlink() or dp.is_symlink():
            try:
                if not (sp.is_symlink() and dp.is_symlink() and os.readlink(sp) == os.readlink(dp)):
                    return False
            except OSError:
                return False
        elif sp.is_dir() or dp.is_dir():
            if not (sp.is_dir() and dp.is_dir()):
                return False
        elif sp.is_file() or dp.is_file():
            if not _same_file_content(sp, dp):
                return False
    return True


def _remove_path(path: Path) -> None:
    if _is_link_like(path):
        try:
            path.unlink()
        except OSError:
            path.rmdir()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _cmd_escape(value: Path | str) -> str:
    """Escape a path passed as an argv item to ``cmd.exe``.

    ``subprocess`` quotes argv items containing whitespace before handing
    them to cmd.exe.  Inside those quotes, caret-escaping ``&`` would become
    part of the literal path, so only escape metacharacters on unquoted
    paths.  This keeps both ``source&folder`` and ``source & folder`` safe.
    """
    text = str(value)
    if any(char.isspace() for char in text):
        return text
    return re.sub(r"([&|<>^])", lambda match: "^" + match.group(1), text)


def make_link(src: Path, dst: Path, *, is_dir: bool, on_backup_failure: Callable[[Path], None] | None = None) -> bool:
    """Ensures dst points at src. POSIX: symlink. Windows: SymbolicLink for
    files, Junction for directories (no elevated privilege needed), falling
    back to a copy if the privilege is missing. Returns True if it changed
    anything on disk.

    A False return is ambiguous ("nothing to do" vs "backup failed, link
    NOT installed") -- callers that can distinguish (env.log) should pass
    on_backup_failure to make the failure mode audible instead of silently
    running on stale content forever (2026-08-15 council-4, Opus 5)."""
    if _points_to(dst, src):
        return False
    # Content check + backup before removal apply on BOTH platforms (2026-
    # 08-15 council-2 review): the Windows-only guard meant a hand-written
    # real file at dst (e.g. ~/.codex/AGENTS.md, a manually configured
    # mcp_config.json) was silently destroyed on POSIX, where _remove_path
    # below just unlinks it. Identical content is backed off only on
    # Windows (where the link may be impossible, so the identical copy is
    # kept); on POSIX an identical real file still falls through to the
    # symlink -- nothing is lost, and the link is the invariant this
    # function promises. Diverging content is backed up before the link
    # replaces it, on every platform.
    if dst.exists() and not _is_link_like(dst):
        if is_dir and _same_tree_content(src, dst):
            if IS_WINDOWS:
                return False
        elif not is_dir and _same_file_content(src, dst):
            if IS_WINDOWS:
                return False
        else:
            # Content differs and this isn't a link: reached when a previous
            # run fell back to a real copy (no symlink/junction privilege, or
            # POSIX before this fix) and the content has since diverged --
            # possibly a local edit, not necessarily just staleness. Back it
            # up before removing instead of destroying it silently (found in
            # a cross-vendor audit, 2026-07-09). Dedup (2026-08-15 council,
            # Opus 5): a tool that rewrites dst every run (atomic
            # write-replace that breaks the symlink, a renomalizing editor)
            # must not pile up one .bak per sync run -- skip if an existing
            # backup already carries the identical bytes. glob.escape():
            # dst.name with glob metacharacters ([, ], ?, *) must not be
            # read as a pattern (2026-08-15 council-2, Opus 5). any(), not
            # sorted(): only the truth value is needed, and only matching
            # types are compared (2026-08-15 council-4, Opus 5).
            ts = time.strftime("%Y%m%d-%H%M%S")
            bak_base = dst.name + ".local-edit.bak-"
            existing = any(
                p for p in dst.parent.glob(glob.escape(bak_base) + "*")
                if (dst.is_dir() and p.is_dir() and _same_tree_content(dst, p))
                or (not dst.is_dir() and p.is_file() and _same_file_content(dst, p))
            )
            if not existing:
                # pid in the name: two concurrent sync runs in the same
                # second would collide on the same .bak name and fail the
                # backup (2026-08-15 council-4, Opus 5).
                bak = dst.with_name(f"{bak_base}{ts}-{os.getpid()}")
                try:
                    if dst.is_dir():
                        shutil.copytree(dst, bak)
                    else:
                        shutil.copy2(dst, bak)
                except OSError:
                    # Backup failed (locked file, permission denied, ...): do
                    # not fall through to _remove_path below without a
                    # confirmed backup, that would silently destroy a local
                    # edit with nothing to restore it from. Bail out and
                    # retry on the next run. Found in a full-codebase audit,
                    # Gemini via agy, 2026-07-09.
                    if on_backup_failure is not None:
                        on_backup_failure(dst)
                    return False
                # Retention (2026-08-15 council-8, Opus 5): the dedup above
                # only skips IDENTICAL content -- a dst rewritten with
                # different bytes on every run (config with a session id,
                # an editor that renormalizes) would otherwise pile one
                # .bak per sync forever. Keep the 3 newest, same convention
                # as _backup_before_migration.
                backups = sorted(dst.parent.glob(glob.escape(bak_base) + "*"), key=lambda p: p.name)
                for stale in backups[:-3]:
                    try:
                        if stale.is_dir() and not stale.is_symlink():
                            shutil.rmtree(stale)
                        else:
                            stale.unlink()
                    except OSError:
                        pass
    _remove_path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not IS_WINDOWS:
        try:
            dst.symlink_to(src, target_is_directory=is_dir)
        except FileExistsError:
            # Two overlapping agent-sync runs (cron timer + manual run) can
            # both pass _remove_path above and then race here -- if the
            # other one already created the same correct link, that's a race
            # we lost harmlessly, not a real error. Found in a full-codebase
            # audit, Gemini via agy, 2026-07-09.
            if not (_is_link_like(dst) and dst.resolve() == src.resolve()):
                raise
        return True
    if is_dir:
        r = _run_external(["cmd.exe", "/d", "/c", "mklink", "/J",
                           _cmd_escape(dst), _cmd_escape(src)],
                           timeout=15, capture_output=True, text=True)
        if r.returncode == 0:
            return True
        shutil.copytree(src, dst)
        return True
    try:
        dst.symlink_to(src)
        return True
    except OSError:
        shutil.copy2(src, dst)
        return True


def resolve_cmd(name: str) -> str | None:
    """shutil.which with OS-specific candidate names (npx/npx.cmd,
    python3/py) — used for optional external tools (systemctl, notify-send)."""
    candidates = {"python3": ["python3", "py"], "npx": ["npx", "npx.cmd"]}.get(name, [name])
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    return None


def _process_running(name: str) -> bool:
    """Detect a live CLI, including npm-installed ``node.exe`` wrappers."""
    try:
        if not IS_WINDOWS:
            r = _run_external(["pgrep", "-x", name], timeout=15, capture_output=True)
            if r.returncode == 0:
                return True
            # npm-installed CLIs (Claude Code et al.) run under node, so
            # `pgrep -x claude` misses them entirely -- and mcp_render's
            # "is Claude open?" guard then rewrites .claude.json while
            # Claude is live, the exact config-loss this guard exists to
            # prevent (same npm-wrapper hole the Windows branch below
            # already probes for via node.exe command lines). re.escape
            # keeps a caller-supplied name with regex metacharacters from
            # over-matching the ERE (2026-08-15 council, Opus 5); the
            # [n]ode bracket is unnecessary here (pgrep never reports
            # itself, and this spawn passes a list, not a shell) but is
            # kept because it costs nothing and defeats accidental
            # self-matching in any future shell-wrapped invocation.
            r = _run_external(["pgrep", "-f", f"[n]ode.*{re.escape(name)}"], timeout=15, capture_output=True)
            return r.returncode == 0
        powershell_query = (
            "(Get-CimInstance Win32_Process -Filter \"Name = 'node.exe'\").CommandLine"
        )
        for probe in (
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", powershell_query],
            ["wmic.exe", "process", "where", "name='node.exe'", "get", "CommandLine"],
        ):
            try:
                r = _run_external(probe, timeout=15, capture_output=True, text=True)
            except OSError:
                continue
            if r.returncode == 0 and name.casefold() in (r.stdout or "").casefold():
                return True
        r = _run_external(["tasklist", "/FI", f"IMAGENAME eq {name}.exe"], timeout=15, capture_output=True, text=True)
        return name.lower() in r.stdout.lower()
    except (OSError, FileNotFoundError):
        return False


def _post_form(url: str, fields: dict) -> bool:
    try:
        data = urllib.parse.urlencode(fields).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception:
        return False


def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """write_text() truncates then writes: a live CLI re-reading the same
    file (settings.json, CLAUDE.md, the systemd unit...) while the 30-minute
    recurring timer regenerates it can catch a truncated/empty file. Write to
    a same-directory temp file and os.replace() it in, which POSIX/Windows
    both guarantee atomic for a rename onto an existing path. Copies the
    existing file's mode onto the temp file first: os.replace is a rename,
    not an in-place write, so without this a plain rewrite would silently
    reset any non-default permission bits to the process umask."""
    old_mode = None
    if path.exists():
        try:
            old_mode = path.stat().st_mode
        except OSError:
            pass
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(content, encoding=encoding)
    if old_mode is not None:
        try:
            os.chmod(tmp, old_mode)
        except OSError:
            pass
    delay = 0.05
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(delay)
            delay *= 2


def _write_if_different(path: Path, content: str) -> bool:
    """Writes content to path unless it's already a regular file with the
    identical content (mirrors agent-sync.sh's write_claude_pointer guard:
    a symlink or differing content always gets replaced)."""
    if path.exists() and not path.is_symlink():
        try:
            if path.read_text(encoding="utf-8") == content:
                return False
        except (OSError, UnicodeDecodeError):
            pass
    if path.is_symlink() or path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, content)
    return True


def _git(env: Env, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(env.vault_data), *args],
                               capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def _run_python_script(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess:
    """subprocess.run for a Python helper script (render.py, skills-sync.py)
    invoked from phases that run inside the host-wide sync lock: a hang here
    must never hold that lock forever (the risk `_git`'s own timeout= above
    already guards against for git itself). TimeoutExpired is caught and
    turned into a synthetic non-zero CompletedProcess, matching `_git`'s own
    pattern, so every call site's existing "non-zero exit code -> best-
    effort, continue" handling covers a timeout too -- without this, the
    exception would propagate out of a for-loop mid-iteration (mcp_render
    renders 4 CLIs in one loop) and silently skip whatever the loop had
    left to do, not just the one CLI that actually hung."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        return subprocess.CompletedProcess(args, 1, stdout, f"timed out after {timeout}s")


def _run_external(args: list[str], *, timeout: int, **kw) -> subprocess.CompletedProcess:
    """subprocess.run for a short-lived external tool (mklink, pgrep,
    tasklist, systemctl, schtasks.exe, notify-send) invoked from phases that
    run inside the host-wide sync lock: same TimeoutExpired-swallowing
    pattern as _run_python_script above, so a hung external command degrades
    to a non-zero CompletedProcess (every call site already treats rc!=0 as
    "best-effort, log, continue") instead of holding the lock -- or the
    whole run -- forever."""
    try:
        return subprocess.run(args, timeout=timeout, **kw)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        return subprocess.CompletedProcess(args, 1, stdout, f"timed out after {timeout}s")


# How long to wait for the host-wide lock before giving up. The default used
# to be 2 seconds, which is shorter than a normal run: a guard cycle takes
# several seconds and an apply can take much longer, so anything starting
# while one was in flight -- the 30-minute timer meeting an interactive run,
# or vault-push meeting either -- gave up almost immediately and reported the
# machine busy. Waiting is the correct behaviour for a lock this coarse: the
# holder finishes and the waiter proceeds, instead of failing for a reason
# the user cannot see and cannot reproduce on demand.
LOCK_TIMEOUT_DEFAULT = "30"


class SyncRunLock:
    """Small standard-library cross-platform lock for the whole sync run."""

    def __init__(self, path: Path, *, timeout: float = float(LOCK_TIMEOUT_DEFAULT)) -> None:
        self.path = path
        self.timeout = max(0.0, timeout)
        self.acquired = False
        self._fh = None

    def __enter__(self) -> "SyncRunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            # Best-effort seed byte. On Windows another process may already
            # hold an msvcrt byte-range lock on byte 0 of a still-empty file
            # (exactly the "lock is busy" case): the write/flush then raises
            # PermissionError, and letting it propagate turned a clean
            # exit-75 "busy" into an uncaught crash. Swallow it -- the lock
            # loop below re-detects the contention and returns not-acquired.
            try:
                self._fh.write(b"0")
                self._fh.flush()
            except OSError:
                pass
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fh.seek(0)
                if IS_WINDOWS:
                    import msvcrt
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
                return self
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    return self
                time.sleep(0.1)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fh is None:
            return
        try:
            if self.acquired:
                self._fh.seek(0)
                if IS_WINDOWS:
                    import msvcrt
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            # close() re-flushes any buffered seed byte; on Windows that can
            # re-raise the same PermissionError the busy-lock path already
            # tolerated in __enter__. A close failure when we never acquired
            # the lock is harmless -- don't let it clobber the caller's
            # exit-75 return with a crash.
            try:
                self._fh.close()
            except OSError:
                pass


class PullState(Enum):
    FRESH = "fresh"
    LOCAL_ONLY = "local_only"
    WRONG_BRANCH = "wrong_branch"
    CONFLICTED = "conflicted"
    DIRTY = "dirty"
    REMOTE_MISSING = "remote_missing"
    FETCH_FAILED = "fetch_failed"
    AHEAD = "ahead"
    DIVERGED = "diverged"
    ERROR = "error"


@dataclass(frozen=True)
class PullOutcome:
    state: PullState
    message: str

    @property
    def allows_apply(self) -> bool:
        return self.state in {PullState.FRESH, PullState.LOCAL_ONLY}


# ── 0.5 data_migrations ──────────────────────────────────────────────────
# Schema version of the DATA the engine reads (manifest.yaml,
# skills.manifest.yaml, USER-PROFILE.md, ...) -- separate from the engine's
# own release version (VERSION file). Bump TARGET_SCHEMA_VERSION and add an
# entry to MIGRATIONS whenever a future engine release needs to reshape an
# existing data file. Today's data shape IS version 1: MIGRATIONS is empty
# on purpose, not a stub -- there is nothing to migrate from yet.
TARGET_SCHEMA_VERSION = 1

# from_version -> callable(env) that migrates from_version to from_version+1
# and returns the list of paths it modified. Each migration is responsible
# for calling _backup_before_migration(env, [affected_paths]) itself BEFORE
# writing anything, then applying the change, then returning the touched
# paths. Populate this dict in future releases; keep it empty otherwise.
MIGRATIONS: dict[int, Callable[["Env"], list[Path]]] = {}


def _backup_before_migration(env: Env, paths: list[Path]) -> None:
    """Same .bak-<timestamp> + keep-3 convention as render.py's backups.
    A migration function must call this BEFORE writing, on the pre-migration
    content -- never after, or the backup would just be a copy of the new
    (already migrated) file."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    for path in paths:
        if not path.is_file():
            continue
        bak = path.with_name(path.name + ".bak-" + ts)
        shutil.copy2(path, bak)
        backs = sorted(path.parent.glob(path.name + ".bak-*"))
        for stale in backs[:-3]:
            stale.unlink(missing_ok=True)


def data_migrations(env: Env) -> bool:
    schema_file = env.vault_data / "99-INDEX" / "DATA-SCHEMA-VERSION.txt"
    if schema_file.is_file():
        try:
            current = int(schema_file.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            env.log(f"data-migrations: {schema_file} has non-numeric content, leaving data untouched")
            return False
    else:
        # No marker yet: today's data shape already IS the target version,
        # there is nothing to migrate -- just stamp the baseline.
        current = TARGET_SCHEMA_VERSION
        if _write_if_different(schema_file, f"{current}\n"):
            env.log(f"data-migrations: stamped {schema_file} at v{current}")

    if current > TARGET_SCHEMA_VERSION:
        env.log(f"data-migrations: data schema v{current} is newer than this engine supports (v{TARGET_SCHEMA_VERSION}) -- leaving data untouched, upgrade the engine")
        return False

    while current < TARGET_SCHEMA_VERSION:
        step = MIGRATIONS.get(current)
        if step is None:
            env.log(f"data-migrations: no migration registered for v{current} -> v{current + 1}, stopping (data left at v{current})")
            return False
        touched = step(env)
        # Stamped right after THIS step succeeds, not only once the whole
        # chain reaches TARGET_SCHEMA_VERSION: a crash partway through a
        # multi-step chain must not force a retry to redo an already-applied
        # (and possibly non-idempotent) earlier step (finding 22).
        current += 1
        if _write_if_different(schema_file, f"{current}\n"):
            env.log(f"data-migrations: stamped {schema_file} at v{current}")
        env.log(f"data-migrations: applied v{current - 1} -> v{current}, touched {touched}")

    return True


def preflight(env: Env) -> bool:
    """Reject invalid data before this run changes a generated runtime file.

    The remote/host declaration is validated while ``Env`` is constructed.
    This phase covers the remaining data inputs used by apply: MCP, optional
    Council seats, skills, and the Claude hooks section that we may merge.
    """
    manifest_path = env.instance_ul / "mcp" / "manifest.yaml"
    council_path = env.instance_ul / "council" / "seats.yaml"
    settings_path = env.home / ".claude" / "settings.json"
    try:
        load_mcp_manifest(manifest_path)
        if council_path.exists():
            load_council_config(council_path)
        validate_claude_settings(settings_path)
    except ConfigValidationError as exc:
        env.log(f"preflight: BLOCKED ({exc})")
        return False

    skills_sync = env.engine_scripts / "skills-sync.py"
    if not skills_sync.is_file():
        env.log(f"preflight: missing skills validator {skills_sync}")
        return False
    result = _run_python_script([sys.executable, str(skills_sync), "--validate"])
    _append_log(env, result.stdout, result.stderr)
    if result.returncode != 0:
        env.log("preflight: skills manifest or local source is invalid")
        return False

    env.log("preflight: MCP, Council, skills, Claude settings, and host remote config are valid")
    return True


# ── 1. pull ──────────────────────────────────────────────────────────────

def pull(env: Env) -> PullOutcome:
    if env.remote in ("local", "none"):
        env.log("pull: skipped (Local-Only mode)")
        return PullOutcome(PullState.LOCAL_ONLY, "Local-Only mode")
    if _git(env, "remote", "get-url", env.remote).returncode != 0:
        message = f"no authoritative remote '{env.remote}' configured"
        env.log(f"pull: blocked ({message})")
        return PullOutcome(PullState.REMOTE_MISSING, message)
    # A rebase/merge left in conflict also leaves HEAD detached (or the tree
    # dirty), so this must be checked BEFORE the symbolic-ref probe and the
    # DIRTY check below -- otherwise it is misreported as a plain
    # WRONG_BRANCH/"detached HEAD" or a generic "uncommitted tracked
    # changes", with no mention of the conflict and no named remedy.
    git_dir_r = _git(env, "rev-parse", "--git-dir")
    if git_dir_r.returncode == 0:
        git_dir = Path(git_dir_r.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = env.vault_data / git_dir
        if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
            message = "a git rebase is in progress in the vault -- run 'git rebase --abort' inside the vault, then re-run agent-sync"
            env.log(f"pull: blocked ({message})")
            return PullOutcome(PullState.CONFLICTED, message)
        if (git_dir / "MERGE_HEAD").exists():
            message = "a git merge is in progress in the vault -- run 'git merge --abort' inside the vault, then re-run agent-sync"
            env.log(f"pull: blocked ({message})")
            return PullOutcome(PullState.CONFLICTED, message)
    current = _git(env, "symbolic-ref", "--quiet", "--short", "HEAD")
    if current.returncode != 0 or current.stdout.strip() != env.branch:
        found = current.stdout.strip() or "detached HEAD"
        message = f"current branch is {found}, expected {env.branch}"
        env.log(f"pull: blocked ({message})")
        return PullOutcome(PullState.WRONG_BRANCH, message)
    status = _git(env, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        message = "cannot inspect tracked working-tree state"
        env.log(f"pull: blocked ({message})")
        return PullOutcome(PullState.ERROR, message)
    if status.stdout.strip():
        message = "the vault has uncommitted tracked changes"
        env.log(f"pull: blocked ({message}; untracked files do not block)")
        return PullOutcome(PullState.DIRTY, message)
    r = _git(env, "fetch", "--prune", env.remote, env.branch)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip()
        message = f"fetch of {env.remote}/{env.branch} failed" + (f": {detail}" if detail else "")
        env.log(f"pull: blocked ({message})")
        return PullOutcome(PullState.FETCH_FAILED, message)
    lh = _git(env, "rev-parse", env.branch)
    rh = _git(env, "rev-parse", f"{env.remote}/{env.branch}")
    mb = _git(env, "merge-base", env.branch, f"{env.remote}/{env.branch}")
    if lh.returncode or rh.returncode or mb.returncode:
        message = f"cannot compare local branch with {env.remote}/{env.branch}"
        env.log(f"pull: blocked ({message})")
        return PullOutcome(PullState.ERROR, message)
    lh, rh, mb = lh.stdout.strip(), rh.stdout.strip(), mb.stdout.strip()
    if lh == rh:
        env.log("pull: already up to date")
        return PullOutcome(PullState.FRESH, "already up to date")
    elif mb == lh:
        if _git(env, "merge", "--ff-only", f"{env.remote}/{env.branch}").returncode == 0:
            env.log(f"pull: fast-forwarded from {env.remote}/{env.branch}")
            return PullOutcome(PullState.FRESH, "fast-forwarded")
        else:
            message = f"fast-forward from {env.remote}/{env.branch} failed"
            env.log(f"pull: blocked ({message})")
            return PullOutcome(PullState.ERROR, message)
    elif mb == rh:
        message = f"local branch is ahead of {env.remote}/{env.branch}"
        env.log(f"pull: blocked ({message})")
        return PullOutcome(PullState.AHEAD, message)
    else:
        message = f"local branch diverged from {env.remote}/{env.branch}"
        env.log(f"pull: blocked ({message}; manual resolution required)")
        return PullOutcome(PullState.DIVERGED, message)


# ── 2. instructions ──────────────────────────────────────────────────────
# NOTE (B2.5 reconciliation, see the launch report): before the two OS twins
# were unified into this one script, agent-sync.ps1 used to actively
# re-link ~/ANTIGRAVITY.md, while agent-sync.sh's own comment (from the same
# era) recorded a verified behavioral probe: Antigravity never reads that
# file, it was dead wiring copied from the Codex pattern. That fact isn't
# OS-dependent, so the fix applies uniformly below on both platforms: never
# re-create ~/ANTIGRAVITY.md, only clean up a leftover symlink if one is
# still there from before this fix. There is no separate Windows code path
# left to diverge from it -- agent-sync.ps1 is now only a launcher for this
# script (see the module docstring).

def instructions(env: Env) -> bool:
    canon = env.instance_ul / "instructions" / "AGENTS.md"
    if not canon.is_file():
        env.log(f"WARNING: missing {canon} — instructions not relinked")
        return False
    claude_md = env.home / "CLAUDE.md"
    content = (
        "# Claude compatibility pointer\n\n"
        "Canonical instructions live at:\n"
        f"{canon}\n\n"
        "At session start, read and follow that file when the user-specific agent policy is needed.\n"
        "Do not duplicate the full bootstrap in CLAUDE.md.\n"
    )
    # Back up before the unconditional overwrite below, on every platform.
    # This file is written directly via _write_if_different(), never through
    # make_link() -- so make_link()'s own .local-edit.bak safety net (which
    # anyway only fires for its Windows real-copy fallback, see its
    # docstring) never applied here in the first place. A hand edit to
    # ~/CLAUDE.md (the user's own home directory) was destroyed the moment
    # its content stopped matching the canonical pointer text, on Linux and
    # Windows alike, with nothing to restore it from. Same
    # `.pre-<reason>-<timestamp>.bak` convention as the systemd units and the
    # OpenCode instructions merge elsewhere in this file.
    if claude_md.is_file() and not claude_md.is_symlink():
        try:
            unchanged = claude_md.read_text(encoding="utf-8") == content
        except (OSError, UnicodeDecodeError):
            unchanged = False
        if not unchanged:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(claude_md, claude_md.with_name(f"CLAUDE.md.pre-instructions-{stamp}.bak"))
            env.log(f"instructions: backed up {claude_md} before rewriting the pointer")
    if _write_if_different(claude_md, content):
        env.log(f"instructions: wrote Claude pointer {claude_md}")

    for target in (env.home / ".gemini" / "config" / "AGENTS.md", env.home / ".codex" / "AGENTS.md"):
        try:
            same = target.is_symlink() and target.resolve() == canon.resolve()
        except OSError:
            same = False
        if same:
            continue
        if make_link(canon, target, is_dir=False, on_backup_failure=lambda dst: env.log(
            f"instructions: WARNING could not back up {dst} (locked or read-only?) -- "
            "left in place, CLI keeps reading divergent content until the next run"
        )):
            env.log(f"instructions: relinked {target}")
        if not _is_link_like(target):
            # No symlink privilege on this host — Windows with developer mode
            # off is the ordinary case — so make_link() fell back to a real
            # copy. The copy is re-aligned on every run, so it is correct as of
            # now; what it is NOT is self-maintaining. Edit the canonical
            # bootstrap and this file stays behind until the next run, with the
            # other CLIs reading yesterday's rules in the meantime. That
            # happened for a full afternoon on 2026-07-26. The fallback stays
            # (it is the only thing that works without the privilege), but it
            # stops being silent.
            env.log(
                f"instructions: NOTE {target} is a real copy, not a link "
                "(no symlink privilege on this host) — it re-aligns only when "
                "agent-sync runs, so a canonical edit is invisible to that CLI "
                "until the next run"
            )

    antigravity_md = env.home / "ANTIGRAVITY.md"
    try:
        if antigravity_md.is_symlink() and antigravity_md.resolve() == canon.resolve():
            antigravity_md.unlink()
            env.log("instructions: removed dead symlink ~/ANTIGRAVITY.md (Antigravity doesn't read it)")
    except OSError:
        pass

    if IS_WINDOWS:
        for src_name, target_name in (("GEMMA.md", "GEMMA.md"), ("LOCAL-WORKER.md", "LOCAL-WORKER.md")):
            src = env.instance_ul / "instructions" / src_name
            if src.is_file() and make_link(src, env.home / target_name, is_dir=False):
                env.log(f"instructions: relinked {target_name}")

    _sync_opencode_instructions(env, canon)
    return True


def _sync_opencode_instructions(env: Env, canon: Path) -> None:
    # OpenCode has no separate pointer/symlink mechanism like Claude/Gemini/
    # Codex above: the canonical bootstrap path is an entry in OpenCode's
    # own top-level "instructions" array (confirmed against a real working
    # config, not guessed). Was previously never written by this provisioner
    # at all -- a fresh install left OpenCode with no bootstrap pointer, and
    # agent-doctor's "OpenCode instructions -> AGENTS.md" check failed
    # permanently with no code path that could ever fix it.
    oc_path = _opencode_config_path(env.home)
    if not oc_path.is_file():
        env.log(f"instructions: {oc_path.name} not present (OpenCode never launched yet) -- skipping")
        return
    raw = oc_path.read_text(encoding="utf-8")
    try:
        config = parse_jsonc(raw) if oc_path.suffix == ".jsonc" else json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        env.log(f"instructions: {oc_path.name} not valid JSON/JSONC; skipping instructions merge")
        return
    if not isinstance(config, dict):
        env.log(f"instructions: {oc_path.name} root is not an object; skipping instructions merge")
        return
    try:
        # JSON paths stay slash-stable on every host.  Windows Path.__str__
        # uses backslashes, while older private templates used forward
        # slashes; treating those spellings as different loaded AGENTS.md
        # twice in OpenCode and paid the bootstrap cost twice per session.
        canon_entry = "~/" + canon.relative_to(env.home).as_posix()
    except ValueError:
        canon_entry = canon.as_posix()
    entries = config.setdefault("instructions", [])
    if not isinstance(entries, list):
        env.log(f"instructions: {oc_path.name} 'instructions' is not a list; skipping instructions merge")
        return

    def instruction_identity(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().replace("\\", "/")
        if normalized.startswith("~/"):
            normalized = (env.home / normalized[2:]).as_posix()
        while "//" in normalized and "://" not in normalized:
            normalized = normalized.replace("//", "/")
        normalized = normalized.rstrip("/")
        return normalized.casefold() if IS_WINDOWS else normalized

    canonical_ids = {
        instruction_identity(canon_entry),
        instruction_identity(canon.as_posix()),
    }
    canonical_ids.discard(None)
    canonical_seen = False
    reconciled: list[object] = []
    for entry in entries:
        if instruction_identity(entry) in canonical_ids:
            if not canonical_seen:
                reconciled.append(canon_entry)
                canonical_seen = True
            continue
        reconciled.append(entry)
    if not canonical_seen:
        reconciled.append(canon_entry)
    if reconciled == entries:
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(oc_path, oc_path.with_name(f"{oc_path.name}.pre-instructions-{stamp}.bak"))
    if oc_path.suffix == ".jsonc":
        updated = set_jsonc_top_level_value(raw, "instructions", reconciled)
    else:
        config["instructions"] = reconciled
        updated = json.dumps(config, indent=2) + "\n"
    _atomic_write_text(oc_path, updated)
    env.log(f"instructions: reconciled one canonical AGENTS.md entry in {oc_path.name} ({oc_path})")


# ── 2.5 antigravity_mcp ──────────────────────────────────────────────────
# Distributes the ONE file mcp_render (below) generates from the manifest to
# Antigravity's other config paths. Not a second generator: render.py is the
# single source of truth, this section is pure fan-out via symlink/junction.

def antigravity_mcp(env: Env) -> bool:
    src = env.home / ".gemini" / "antigravity" / "mcp_config.json"
    if not src.is_file():
        return True
    for target in (
        env.home / ".gemini" / "antigravity-cli" / "mcp_config.json",
        env.home / ".gemini" / "antigravity-ide" / "mcp_config.json",
        env.home / ".gemini" / "config" / "mcp_config.json",
    ):
        try:
            same = target.is_symlink() and target.resolve() == src.resolve()
        except OSError:
            same = False
        if same:
            continue
        make_link(src, target, is_dir=False, on_backup_failure=lambda dst: env.log(
            f"mcp: WARNING could not back up {dst} (locked or read-only?) -- "
            "left in place, Antigravity keeps reading the divergent copy"
        ))
        env.log(f"mcp: relinked {target}")
    return True


# ── 2.7 utils ────────────────────────────────────────────────────────────
# LINKED_COMMANDS is the single source for every bare command utils() puts
# on PATH -- both the POSIX (symlink onto a *.sh twin) and Windows (real
# *.ps1 target shim + *.cmd wrapper) branches below consume the SAME dict
# instead of each carrying their own hardcoded list. Real bug history this
# closes (2026-07-13 review, four separate commits over the same root
# cause): agent-sync, agent-doctor, vault-groom and firecrawl-local were all
# documented everywhere as bare commands while nothing ever actually linked
# them -- a hardcoded list in one branch is exactly the kind of place a new
# command silently falls through the cracks of.
#   source:   'engine' -> env.engine_scripts, 'vault' -> env.vault_scripts.
#     Executable utilities belong to the engine even when they mutate Vault
#     data: after the engine/data split, a Vault-side wrapper has no sibling
#     agent_sync.py and cannot run the authoritative implementation.
#   posix/windows: whether a same-named <name>.sh / <name>.ps1 twin ships
#     and should be linked on that OS (vault-ocr-local remains POSIX-only by
#     design, while firecrawl-local ships a native .ps1 twin).
#   optional: bring-your-own -- absence of the source is the documented
#     default, not a failure (see _link_util below).
# agent-skill is deliberately NOT here: utils() generates its wrapper from a
# template at write time (there is no agent-skill.sh/.ps1 twin to symlink),
# a different enough shape that folding it into this table would obscure
# rather than simplify it.
LINKED_COMMANDS: dict[str, dict[str, object]] = {
    "agent-sync":      {"source": "engine", "posix": True,  "windows": True},
    "agent-doctor":    {"source": "engine", "posix": True,  "windows": True},
    "agent-chrome":    {"source": "engine", "posix": True,  "windows": True},
    "agent-now":       {"source": "engine", "posix": True,  "windows": True},
    "agent-open-folder": {"source": "engine", "posix": True, "windows": True},
    "council":         {"source": "engine", "posix": True,  "windows": True},
    "firecrawl-local": {"source": "engine", "posix": True,  "windows": True},
    "nexgen-update":   {"source": "engine", "posix": True,  "windows": True},
    "vault-push":      {"source": "engine", "posix": True,  "windows": True},
    "vault-groom":     {"source": "engine", "posix": True,  "windows": True},
    "vault-ocr-local": {"source": "vault",  "posix": True,  "windows": False, "optional": True},
}


def _linked_command_src_dir(env: Env, source: str) -> Path:
    return env.engine_scripts if source == "engine" else env.vault_scripts


def _link_util(src: Path, dst: Path, env: Env, label: str, *, optional: bool = False) -> bool:
    if not src.is_file():
        if optional:
            # Documented as bring-your-own, same as local-model-agent.ps1
            # (LOCAL-WORKER.md) and the semantic-search backend (README):
            # vault-ocr-local.sh is referenced by AGENTS.md/vault-ocr.md but
            # never actually shipped in 03-INFRA/scripts -- verified absent
            # from `git ls-files`, not a sandbox/test-fixture artifact.
            # Absence here is the documented default, not a failure.
            env.log(f"utils: missing source {src} (optional, bring-your-own)")
            return True
        # A missing REQUIRED source means the engine checkout itself is
        # incomplete -- a real problem, not a benign not-applicable case.
        env.log(f"utils: missing source {src}")
        return False
    if not IS_WINDOWS and not (src.stat().st_mode & 0o111):
        env.log(f"utils: source {src} is not executable, refusing to mutate an engine source")
        return False
    try:
        same = dst.is_symlink() and dst.resolve() == src.resolve()
    except OSError:
        same = False
    if same:
        return True
    make_link(src, dst, is_dir=False)
    env.log(f"utils: relinked {label}")
    return True


def _install_linux_browser_desktop_entry(env: Env) -> None:
    """Expose the canonical visible Chrome launcher to Linux URL handlers.

    Writes TWO files under ~/.local/share/applications, and the second one is
    the part that must not be understated:

      agent-chrome.desktop    a new launcher entry. Adding it does NOT make it
                              the default browser: that stays the user's own
                              reversible choice, and the doctor only reports
                              whether it has been made.
      google-chrome.desktop   a hidden entry that SHADOWS the distribution's
                              Chrome launcher in the user's XDG layer. Existing
                              dock icons and direct google-chrome.desktop
                              activations then reach this wrapper.

    The shadowing is deliberate and load-bearing: without it a plain Chrome
    started from the dock wins the first-process race with no CDP port, and the
    whole shared-browser lane silently stops working. But it does change what
    an already-installed icon launches, so it is declared in docs/what-gets-
    written.md and removed by docs/uninstall.md. An earlier version of this
    docstring described only the restraint and not the shadowing, which read as
    if the provisioner left the user's Chrome untouched.
    """
    if platform.system() != "Linux":
        return
    applications = env.home / ".local" / "share" / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    launcher = env.local_bin / "agent-chrome"
    desktop = (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=Google Chrome (NeXgen shared)\n"
        "Comment=Visible shared Chrome with local CDP enabled\n"
        f"Exec={launcher} %U\n"
        "Icon=google-chrome\n"
        "Terminal=false\n"
        "StartupNotify=true\n"
        "Categories=Network;WebBrowser;\n"
        "MimeType=text/html;x-scheme-handler/http;x-scheme-handler/https;\n"
        "StartupWMClass=Google-chrome\n"
    )
    target = applications / "agent-chrome.desktop"
    if _write_if_different(target, desktop):
        env.log(f"utils: installed Linux desktop launcher {target}")
    target.chmod(0o644)
    # Shadow the distribution-owned launcher ID in the user's XDG layer.
    # Existing dock entries and direct `google-chrome.desktop` activations
    # then reach the same wrapper instead of winning the first-process race
    # without CDP. Keep the compatibility entry hidden so the application
    # menu shows one Chrome, not two.
    compatibility = desktop.replace(
        "Name=Google Chrome (NeXgen shared)\n",
        "Name=Google Chrome\nNoDisplay=true\n",
    )
    compatibility_target = applications / "google-chrome.desktop"
    if _write_if_different(compatibility_target, compatibility):
        env.log(f"utils: installed Linux Chrome compatibility redirect {compatibility_target}")
    compatibility_target.chmod(0o644)
    _route_linux_chrome_app_launchers(env, applications, launcher)


# Chrome binary names that may appear as argv[0] of a generated Exec= line.
_CHROME_BINARIES = frozenset(
    {
        "google-chrome",
        "google-chrome-stable",
        "google-chrome-beta",
        "google-chrome-unstable",
        "chrome",
        "chromium",
        "chromium-browser",
    }
)


# Desktop Entry Specification, "Exec": an argument containing a reserved
# character must be quoted, and quoting is done with DOUBLE quotes. shlex.quote
# would emit POSIX single quotes, which the spec does not accept -- fine on a
# path without spaces, silently wrong on one with them.
_DESKTOP_RESERVED = frozenset(' \t\n"\'\\><~|&;$*?#()`')


def _desktop_exec_quote(value: str) -> str:
    if value and not any(char in _DESKTOP_RESERVED for char in value):
        return value
    escaped = value
    for char in ("\\", '"', "`", "$"):
        escaped = escaped.replace(char, f"\\{char}")
    return f'"{escaped}"'


def _route_exec_line(value: str, launcher: Path) -> str | None:
    """Rewrite one Exec= value so it starts the shared browser, or None.

    Returns None when the line is already routed or is not a Chrome launch, so
    the caller can leave the file untouched instead of rewriting it every run.
    """
    try:
        argv = shlex.split(value)
    except ValueError:
        return None  # Unparseable quoting: leave the entry exactly as it is.
    if not argv or Path(argv[0]).name not in _CHROME_BINARIES:
        return None
    # The launcher owns the profile choice. A generated entry that hardcodes
    # --user-data-dir would keep working, but it would silently pin the app to
    # whatever path Chrome recorded when the shortcut was created.
    rest = [arg for arg in argv[1:] if not arg.startswith("--user-data-dir=")]
    return " ".join(_desktop_exec_quote(part) for part in [str(launcher), *rest])


def _route_linux_chrome_app_launchers(env: Env, applications: Path, launcher: Path) -> None:
    """Route Chrome's generated PWA entries through the shared-browser launcher.

    Shadowing google-chrome.desktop (above) only covers the browser icon. Chrome
    also writes one chrome-<app-id>-<profile>.desktop per installed web app, and
    those call the Chrome binary directly. That is not a cosmetic gap: the FIRST
    Chrome process to open the shared profile fixes whether :9222 exists for the
    whole session, and a PWA started from the dock starts it with no debugging
    port. Every later launch, agent-chrome included, is then only an IPC handoff
    to that portless browser, so the entire shared-browser lane stays dead until
    Chrome is restarted -- with no error anywhere, because opening the app
    worked fine.

    Chrome rewrites these files whenever a web app is installed or updated, so
    this repair is deliberately idempotent and re-run by the recurring
    `agent-sync guard` timer rather than done once at install time.
    """
    for entry in sorted(applications.glob("chrome-*.desktop")):
        try:
            original = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = original.splitlines(keepends=True)
        changed = False
        for index, line in enumerate(lines):
            stripped = line.rstrip("\n")
            if not stripped.startswith("Exec="):
                continue
            routed = _route_exec_line(stripped[len("Exec=") :], launcher)
            if routed is None:
                continue
            lines[index] = f"Exec={routed}\n" if line.endswith("\n") else f"Exec={routed}"
            changed = True
        if not changed:
            continue
        mode = entry.stat().st_mode & 0o777
        if _write_if_different(entry, "".join(lines)):
            env.log(f"utils: routed Chrome web-app launcher {entry.name} through {launcher.name}")
        entry.chmod(mode)


def utils(env: Env) -> bool:
    env.local_bin.mkdir(parents=True, exist_ok=True)
    skill_source = env.engine_scripts / "agent-skill.py"
    if not IS_WINDOWS:
        healthy = True
        # agent-sync/agent-doctor themselves, not just the tools they manage:
        # utils() is a phase THIS SAME agent-sync run executes, so the first
        # invocation ever has to happen by full path (INIT.md already
        # documents that correctly) -- but nothing then created the bare
        # command for every run after. Two concrete real-world consequences,
        # not just tidiness: _install_systemd_units() (below) writes the
        # recurring guard timer's ExecStart as bare '.local/bin/agent-sync
        # guard', which pointed at a symlink no code path ever created; and
        # _persisted_engine_root() reads that same symlink to detect an
        # existing engine-root cutover, silently dead for anyone who never
        # got a working one.
        for name, cfg in LINKED_COMMANDS.items():
            if not cfg["posix"]:
                continue
            src_dir = _linked_command_src_dir(env, cfg["source"])
            src = src_dir / f"{name}.sh"
            healthy = _link_util(src, env.local_bin / name, env, name, optional=bool(cfg.get("optional"))) and healthy
        if skill_source.is_file():
            wrapper = f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(skill_source))} \"$@\"\n"
            target = env.local_bin / "agent-skill"
            if _write_if_different(target, wrapper):
                env.log("utils: installed agent-skill")
            target.chmod(0o755)
        else:
            env.log(f"utils: missing source {skill_source}")
            healthy = False
        _install_linux_browser_desktop_entry(env)
        return healthy
    healthy = True
    for name, cfg in LINKED_COMMANDS.items():
        if not cfg["windows"]:
            continue
        src_dir = _linked_command_src_dir(env, cfg["source"])
        src = src_dir / f"{name}.ps1"
        if not src.is_file():
            env.log(f"utils: missing source {src}")
            healthy = False
            continue
        dst = env.local_bin / f"{name}.ps1"
        # PowerShell resolves $PSScriptRoot to the symlink's directory when a
        # script is launched through a Windows file link. Engine launchers use
        # sibling files (agent_sync.py, render.py, and so on), so a symlink in
        # ~/.local/bin silently points those lookups at the wrong directory.
        # A tiny real shim preserves the target script's own $PSScriptRoot and
        # works identically on hosts with or without symlink privilege.
        if dst.is_symlink():
            _remove_path(dst)
        quoted_src = str(src).replace("'", "''")
        launcher = (
            "$ErrorActionPreference = 'Stop'\r\n"
            f"$Target = '{quoted_src}'\r\n"
            "& $Target @args\r\n"
            "exit $LASTEXITCODE\r\n"
        )
        if _write_if_different(dst, launcher):
            env.log(f"utils: installed {name}.ps1 launcher")
        wrapper = (
            "@echo off\r\n"
            f"powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"%~dp0{name}.ps1\" %*\r\n"
        )
        if _write_if_different(env.local_bin / f"{name}.cmd", wrapper):
            env.log(f"utils: installed {name}.cmd wrapper")
    if skill_source.is_file():
        wrapper = (
            "@echo off\r\n"
            f"\"{sys.executable}\" \"{skill_source}\" %*\r\n"
        )
        if _write_if_different(env.local_bin / "agent-skill.cmd", wrapper):
            env.log("utils: installed agent-skill.cmd")
    else:
        env.log(f"utils: missing source {skill_source}")
        healthy = False
    # Registry-only PATH fix (release-critical: without this, every bare
    # command above resolves only in a terminal that already had
    # ~/.local/bin on PATH from some other source). Best-effort: a registry
    # failure is loud in the log but never flips this phase to failed --
    # the wrappers themselves were still written correctly, and a future
    # doctor check surfaces a PATH that's still missing them.
    _ensure_user_path_entry(env)
    return healthy


def _ensure_user_path_entry(env: Env) -> None:
    """Adds env.local_bin to HKCU\\Environment's Path so bare commands (not
    just full-path invocations) work in a NEW terminal. Windows has no
    always-sourced profile equivalent to POSIX's typical ~/.local/bin
    PATH entry -- without this, every wrapper utils() just wrote is
    reachable only by full path, forever, on a fresh install. Registry
    only: a running terminal's own os.environ is a snapshot from when it
    started and cannot be fixed retroactively, same as a manual `setx`."""
    if not IS_WINDOWS:
        return
    if _host_mutations_disabled(env, "user PATH registry update"):
        return
    try:
        import winreg
    except ImportError as exc:
        env.log(f"path: WARNING -- winreg unavailable ({exc}); add {env.local_bin} to PATH manually")
        return
    target = str(env.local_bin)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                             winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                current, kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                current, kind = "", winreg.REG_EXPAND_SZ
            if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                kind = winreg.REG_EXPAND_SZ
            entries = [e for e in current.split(";") if e.strip()]
            # Case-insensitive, trailing-slash-tolerant: Windows paths are
            # case-insensitive and a prior run (or the user by hand) may
            # have added the same folder with a trailing backslash.
            normalized = {e.strip().rstrip("\\/").lower() for e in entries}
            if target.rstrip("\\/").lower() in normalized:
                env.log(f"path: {target} already on user PATH")
                return
            entries.append(target)
            new_value = ";".join(entries)
            current_process_path = os.environ.get("PATH", "")
            projected_process_length = len(current_process_path) + max(0, len(new_value) - len(current))
            if max(len(new_value), projected_process_length) > WINDOWS_CMD_ENV_LIMIT:
                env.log(
                    "path: WARNING -- refusing to append the launcher directory: "
                    f"the resulting User PATH would be {len(new_value)} characters and the "
                    f"projected process PATH {projected_process_length}, "
                    f"over cmd.exe's {WINDOWS_CMD_ENV_LIMIT}-character inherited-variable limit. "
                    f"Shorten PATH or invoke {target} by absolute path."
                )
                return
            winreg.SetValueEx(key, "Path", 0, kind, new_value)
    except OSError as exc:
        # Loud in the log, not a failed phase (see utils()'s call site): the
        # wrappers this run wrote are still correct, only the PATH entry
        # didn't happen -- a later doctor check can surface and retry it.
        env.log(f"path: WARNING -- could not update user PATH via registry ({exc}); add {target} to PATH manually")
        return
    env.log(f"path: added {target} to user PATH -- open a new terminal for bare commands to work")
    _broadcast_environment_change()


def _broadcast_environment_change() -> None:
    """Tells already-open top-level windows (Explorer, etc.) that the
    environment changed, matching what `setx`/System Properties does after
    an Environment Variables edit. Best-effort only: a NEW terminal already
    picks up the registry value on its own by re-reading HKCU\\Environment
    at process start, so a failure here just means already-open windows
    stay stale a little longer, never a correctness problem."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x1A
        SMTO_ABORTIFHUNG = 0x0002
        result = ctypes.c_long()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
            SMTO_ABORTIFHUNG, 5000, ctypes.byref(result),
        )
    except Exception:
        pass


def local_model_runtime(env: Env) -> bool:
    if not IS_WINDOWS:
        return True
    # The adapter is deliberately private and machine-specific.  The public
    # engine owns only the stable local-worker/local-agent capability names;
    # if a user supplies an adapter, it lives in the private Vault scripts
    # plane and is never copied into the public product repository.
    src = env.vault_scripts / "local-model-agent.ps1"
    if not src.is_file():
        # Absence is the expected default for most installs, not a failure.
        env.log(f"local-model: missing source {src} (optional, bring-your-own)")
        return True
    env.local_bin.mkdir(parents=True, exist_ok=True)
    runtime = env.local_bin / "local-model-agent.ps1"
    if make_link(src, runtime, is_dir=False):
        env.log("local-model: relinked local-model-agent.ps1")
    legacy_wrappers = {
        "gemma-worker.ps1": "-Mode worker @args",
        "gemma-agent.ps1": "-Mode agent @args",
    }
    for old_name, mode_marker in legacy_wrappers.items():
        old = env.local_bin / old_name
        try:
            old_text = old.read_text(encoding="utf-8") if old.is_file() else ""
            managed = old.is_symlink() or (
                old.is_file()
                and "local-model-agent.ps1" in old_text
                and mode_marker in old_text
            )
        except (OSError, UnicodeDecodeError):
            managed = False
        if managed:
            _remove_path(old)
            env.log(f"local-model: removed legacy model-specific alias {old_name}")
    for old_name in ("gemma-worker.cmd", "gemma-agent.cmd"):
        old = env.local_bin / old_name
        if old.is_symlink():
            _remove_path(old)
            env.log(f"local-model: removed legacy model-specific alias {old_name}")
    wrappers = {
        "local-worker.ps1": "$ScriptPath = Join-Path $PSScriptRoot 'local-model-agent.ps1'\r\n& $ScriptPath -Mode worker @args\r\n",
        "local-agent.ps1": "$ScriptPath = Join-Path $PSScriptRoot 'local-model-agent.ps1'\r\n& $ScriptPath -Mode agent @args\r\n",
    }
    changed = False
    for name, content in wrappers.items():
        changed = _write_if_different(env.local_bin / name, content) or changed
    if changed:
        env.log("local-model: installed runtime shims")
    return True


# ── 2.75 scheduler ───────────────────────────────────────────────────────
# Self-healing recurring trigger on EVERY apply/guard, on both OSes: the
# opt-in-only Windows switch (-InstallScheduledTask) was the gap this section
# closes -- install_scheduler() runs as a normal per-run step, not an
# opt-in one.

def _systemd_env_line(key: str, value: str) -> str:
    """Quotes the whole assignment per systemd.syntax(7): unquoted
    Environment= values split on whitespace, so a path with a space (e.g.
    '/opt/agents/nexgen engine') silently truncates the variable instead of
    failing loud. Backslash and double-quote are C-escaped, matching
    systemd's own quoted-string escaping."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'Environment="{key}={escaped}"'


def _systemd_service_content(env: "Env") -> str:
    """Carries AGENT_ENGINE_ROOT/AGENT_VAULT_DATA into the recurring timer.
    The timer re-reads its unit file, not a shell environment, so a one-off
    engine-cutover run (env var passed on the command line) makes the switch
    persistent for every future guard run. env.engine_root is already the
    single source of truth (env var, else the persisted agent-sync symlink,
    else the vault default -- see Env._persisted_engine_root), so a plain
    unadorned 'agent-sync apply' never silently reverts an already-live
    cutover here either."""
    lines = ["[Unit]",
             "Description=KnowledgeVault agent sync guard (pull + apply + healthcheck, no publish)",
             # A guard that dies has to say so itself: waiting for the next run
             # to notice is waiting for the thing that just failed.
             "OnFailure=agent-alert@%n.service",
             "", "[Service]", "Type=oneshot"]
    default_engine_root = (env.vault / "03-INFRA").resolve()
    if env.engine_root.resolve() != default_engine_root:
        lines.append(_systemd_env_line("AGENT_ENGINE_ROOT", str(env.engine_root)))
    # env.vault_data (not the raw env var): a bare run with AGENT_VAULT_DATA
    # unset must not erase an already-persisted cutover the same way a bare
    # AGENT_ENGINE_ROOT-less run used to silently revert engine_root above.
    if env.vault_data.resolve() != env.vault.resolve():
        lines.append(_systemd_env_line("AGENT_VAULT_DATA", str(env.vault_data)))
    scheduler_path = os.pathsep.join([
        str(env.home / ".local" / "bin"),
        str(env.home / ".opencode" / "bin"),
        os.environ.get("PATH", os.defpath),
    ])
    lines.append(_systemd_env_line("PATH", scheduler_path))
    lines.append("ExecStart=%h/.local/bin/agent-sync guard")
    return "\n".join(lines) + "\n"


_SYSTEMD_TIMER = """[Unit]
Description=agent-sync guard every 30 minutes and shortly after login

[Timer]
OnStartupSec=3min
OnUnitActiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
"""


_SYSTEMD_ALERT_TEMPLATE = """[Unit]
Description=Tell the user that %i could not run

[Service]
Type=oneshot
ExecStart=%h/.local/bin/agent-sync notify-failure %i
"""

_SYSTEMD_HEARTBEAT = """[Unit]
Description=Say something when the agent sync has not completed in far too long

[Service]
Type=oneshot
ExecStart=%h/.local/bin/agent-sync heartbeat
"""

_SYSTEMD_HEARTBEAT_TIMER = """[Unit]
Description=Hourly check that the agent sync is still completing

[Timer]
OnStartupSec=5min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
"""


def _install_systemd_units(env: Env) -> bool:
    # Defense in depth, not the primary fix: utils() (which links the bare
    # agent-sync command this timer's ExecStart depends on) always runs
    # before this function in the same apply/guard phases list, so this
    # should never actually fire. It exists for the one edge case where
    # utils() partially fails on an unrelated required link and the phase
    # loop (which does not abort on a single phase's failure) still reaches
    # this one in the same pass. Previously this only warned and then
    # installed/enabled the timer anyway: a recurring 30-minute trigger
    # armed forever against a command that does not exist, failing every
    # cycle instead of self-healing. Skip the whole install/enable instead --
    # a future guard run that reaches a working utils() phase first will
    # retry this function and enable the timer then.
    if not (env.local_bin / "agent-sync").exists():
        warning = (
            "systemd: WARNING -- ~/.local/bin/agent-sync does not exist yet; "
            "skipping timer install/enable instead of arming a recurring job "
            "against a nonexistent command -- retried on a future guard run "
            "once utils() links it"
        )
        env.log(warning)
        # Also stderr, not just the log file: this is defense-in-depth for a
        # case that should never fire (utils() always runs first in the same
        # apply/guard pass) -- if it ever does, it needs to be humanly
        # visible during an interactive apply, not only discoverable by
        # someone who thinks to open agent-sync.log.
        print(warning, file=sys.stderr)
        return False
    unit_dir = env.home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    changed = False
    healthy = True
    for path, content, label in (
        (unit_dir / "agent-sync.service", _systemd_service_content(env), "agent-sync.service set to pull mode"),
        (unit_dir / "agent-sync.timer", _SYSTEMD_TIMER, "agent-sync.timer updated"),
        (unit_dir / "agent-alert@.service", _SYSTEMD_ALERT_TEMPLATE, "agent-alert@.service installed"),
        (unit_dir / "agent-heartbeat.service", _SYSTEMD_HEARTBEAT, "agent-heartbeat.service installed"),
        (unit_dir / "agent-heartbeat.timer", _SYSTEMD_HEARTBEAT_TIMER, "agent-heartbeat.timer installed"),
    ):
        if path.exists():
            try:
                if path.read_text(encoding="utf-8") == content:
                    continue
            except (OSError, UnicodeDecodeError):
                pass
            stamp = time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(path, path.with_name(f"{path.name}.pre-pull-mode-{stamp}.bak"))
        _atomic_write_text(path, content)
        changed = True
        env.log(f"systemd: {label}")
    if not resolve_cmd("systemctl"):
        env.log("systemd: systemctl not found -- unit files written but not enabled")
        return healthy
    if changed:
        r = _run_external(["systemctl", "--user", "daemon-reload"], timeout=30, capture_output=True, text=True)
        if r.returncode != 0:
            env.log(f"systemd: user daemon-reload failed: {(r.stderr or r.stdout).strip()}")
            healthy = False
    # Unconditional, not gated on `changed`: writing the unit files was never
    # enough on its own -- systemd requires an explicit `enable` to create
    # the timers.target.wants/ symlink that actually makes the timer fire.
    # This call was missing entirely before (beta-readiness review,
    # 2026-07-13): a fresh install wrote inert unit files that never ran
    # unless a human happened to `systemctl --user enable` them by hand.
    # --now also starts it immediately rather than waiting for next login.
    r = _run_external(["systemctl", "--user", "enable", "--now",
                       "agent-sync.timer", "agent-heartbeat.timer"],
                       timeout=30, capture_output=True, text=True)
    if r.returncode != 0:
        env.log(
            "systemd: could not enable agent-sync.timer "
            f"({(r.stderr or r.stdout).strip()}) -- the recurring guard will not run. "
            "On a headless/SSH-only box this is often a missing "
            "`loginctl enable-linger $USER` (lets --user units run without an active login session)."
        )
        healthy = False
    return healthy


_VBS_TEMPLATE = (
    'Set shell = CreateObject("WScript.Shell")\r\n'
    'Set processEnv = shell.Environment("PROCESS")\r\n'
    'processEnv("AGENT_ENGINE_ROOT") = "{engine_root}"\r\n'
    'processEnv("AGENT_VAULT_DATA") = "{vault_data}"\r\n'
    'processEnv("KNOWLEDGE_VAULT_PATH") = "{vault}"\r\n'
    'processEnv("KNOWLEDGE_VAULT_BRANCH") = "{branch}"\r\n'
    'script = "{script}"\r\n'
    'shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & script & Chr(34) '
    '& " {mode}", 0, True\r\n'
)


def _scheduled_task_invokes_wrapper(task_name: str, wrapper_path: Path) -> bool:
    """True when the named scheduled task already exists and runs the exact
    wrapper file at wrapper_path.

    The query uses /XML (Task Scheduler's own, locale-independent format)
    rather than /FO LIST /V, whose field labels are localized -- matching on
    a localized "Task To Run" label would silently always-"match" on a
    non-English Windows (and always-"mismatch" on an English one). The
    wrapper path is the only discriminant that matters: Task Scheduler
    executes whatever file sits at that path, so a content-only update of
    the .vbs needs no task rewrite. A task that exists but invokes an
    older/other wrapper path fails the match and gets rewritten."""
    r = _run_external(
        ["schtasks.exe", "/Query", "/TN", task_name, "/XML"],
        timeout=30, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return False
    return str(wrapper_path) in r.stdout


def _install_scheduled_task(env: Env) -> bool:
    if _host_mutations_disabled(env, "Task Scheduler update"):
        return True
    task_name = "KnowledgeVault Agent Sync"
    script_path = env.engine_scripts / "agent-sync.ps1"
    # Mirrors the Linux systemd guard above: the VBS wrapper below shells out
    # to this exact path, so scheduling a recurring task against it while it
    # is missing would arm the same "fires every 30 minutes forever, fails
    # every time" trap the systemd fix closes -- just against a missing
    # engine script instead of a missing ~/.local/bin shim (this scheduler
    # invokes the engine script directly, it does not go through a
    # ~/.local/bin shim the way the systemd unit's ExecStart does).
    if not script_path.is_file():
        warning = (
            f"scheduled-task: WARNING -- {script_path} does not exist yet; "
            "skipping Task Scheduler install/enable instead of arming a "
            "recurring job against a nonexistent script -- retried on a "
            "future guard run once the engine checkout provides it"
        )
        env.log(warning)
        print(warning, file=sys.stderr)
        return False
    # Generated, machine-specific state must never dirty the public engine
    # checkout or risk being staged into a release.
    wrapper_path = env.log_dir / "start-agent-sync-hidden.vbs"
    def vbs_string(value: Path | str) -> str:
        return str(value).replace('"', '""')

    def wrapper_for(mode: str) -> str:
        return _VBS_TEMPLATE.format(
            script=vbs_string(script_path),
            engine_root=vbs_string(env.engine_root),
            vault_data=vbs_string(env.vault_data),
            vault=vbs_string(env.vault),
            branch=vbs_string(env.branch),
            mode=mode,
        )

    content = wrapper_for("guard")
    if _write_if_different(wrapper_path, content):
        env.log("scheduled-task: hidden wrapper updated")
    # Windows twin of agent-heartbeat.timer. Task Scheduler has no usable
    # equivalent of systemd's OnFailure=, but the heartbeat never needed one:
    # it measures elapsed time since the last completed guard, so it catches a
    # guard that failed, one that was cancelled, and one that was never
    # scheduled at all -- the same three cases, without knowing which.
    beat_path = env.log_dir / "start-agent-heartbeat-hidden.vbs"
    if _write_if_different(beat_path, wrapper_for("heartbeat")):
        env.log("scheduled-task: heartbeat wrapper updated")
    beat_task = "KnowledgeVault Agent Heartbeat"
    if not _scheduled_task_invokes_wrapper(beat_task, beat_path):
        r = _run_external(
            ["schtasks.exe", "/Create", "/TN", beat_task, "/SC", "HOURLY",
             "/TR", f'wscript.exe "{beat_path}"', "/F"],
            timeout=30, capture_output=True, text=True)
        if r.returncode == 0:
            env.log(f"scheduled-task: installed/updated '{beat_task}' via schtasks.exe")
        else:
            env.log(f"scheduled-task: heartbeat task failed ({r.stdout}{r.stderr})")

    run_cmd = f'wscript.exe "{wrapper_path}"'
    every30 = ["schtasks.exe", "/Create", "/TN", task_name, "/SC", "MINUTE", "/MO", "30", "/TR", run_cmd, "/F"]
    logon = ["schtasks.exe", "/Create", "/TN", f"{task_name} Logon", "/SC", "ONLOGON", "/TR", run_cmd, "/F"]
    # The every-30-minutes task is the recurring guard and MUST exist, so it
    # is always (re)created when absent. The Logon trigger is a redundant
    # nicety and, on several Windows builds, `schtasks /Create /TR "wscript
    # \"...\""` fails with ERROR_ACCESS_DENIED no matter the quoting: retrying
    # it every 30 minutes is what made the whole scheduler read as a malware
    # persistence pattern. Its creation is therefore attempted only on the
    # first run (no marker) or when the wrapper content changed (hash
    # mismatch); otherwise the Startup-folder VBS copy below covers logon.
    logon_attempt_marker = env.log_dir / "scheduled-task-logon-attempt"
    _content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    _previous_hash = logon_attempt_marker.read_text(encoding="utf-8").strip() if logon_attempt_marker.exists() else ""
    if _scheduled_task_invokes_wrapper(task_name, wrapper_path):
        env.log(f"scheduled-task: '{task_name}' already invokes {wrapper_path.name}; no rewrite")
    else:
        r = _run_external(every30, timeout=60, capture_output=True, text=True)
        if r.returncode != 0:
            env.log(f"scheduled-task: schtasks.exe failed for '{task_name}': {r.stdout}{r.stderr}")
            # The every-30-minutes task IS the recurring guard; without it there
            # is no self-healing trigger at all, unlike the logon trigger below
            # (a redundant nicety the every-30 task already covers within 30min).
            return False
        env.log(f"scheduled-task: installed/updated '{task_name}' via schtasks.exe")
    if _scheduled_task_invokes_wrapper(f"{task_name} Logon", wrapper_path):
        env.log(f"scheduled-task: '{task_name} Logon' already invokes {wrapper_path.name}; no rewrite")
        logon_attempt_marker.write_text(_content_hash, encoding="utf-8")
        return True
    if _previous_hash != _content_hash:
        r = _run_external(logon, timeout=60, capture_output=True, text=True)
        logon_attempt_marker.write_text(_content_hash, encoding="utf-8")
        if r.returncode == 0:
            env.log(f"scheduled-task: installed/updated '{task_name} Logon' via schtasks.exe")
            return True
        env.log(f"scheduled-task: logon trigger failed via schtasks.exe ({r.stdout}{r.stderr}); falling back to Startup folder")
    else:
        env.log(f"scheduled-task: '{task_name} Logon' creation previously failed; not retrying (wrapper unchanged)")
    startup_dir = os.environ.get("APPDATA")
    if startup_dir:
        startup_vbs = Path(startup_dir) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "KnowledgeVault Agent Sync.vbs"
        startup_vbs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wrapper_path, startup_vbs)
        env.log(f"startup: installed hidden logon fallback {startup_vbs}")
    return True


def install_scheduler(env: Env) -> bool:
    if IS_WINDOWS:
        return _install_scheduled_task(env)
    return _install_systemd_units(env)


# ── 2.8 mcp_render ───────────────────────────────────────────────────────
# render.py is already cross-platform (proven by the 4-dialect B1 matrix):
# invoked as a subprocess with sys.executable (the SAME interpreter running
# agent_sync.py), so there is no python3-vs-python-vs-py resolution needed.



def mcp_render(env: Env) -> bool:
    render_path = env.ul / "mcp" / "render.py"
    if not render_path.is_file():
        env.log(f"mcp-gen: missing renderer {render_path}")
        return False
    healthy = True
    for cli in ("opencode", "antigravity", "codex"):
        r = _run_python_script([sys.executable, str(render_path), "--write", cli])
        _append_log(env, r.stdout, r.stderr)
        if r.returncode == 0:
            env.log(f"mcp-gen: {cli} aligned with the manifest")
        elif r.returncode == 3:
            env.log(f"mcp-gen: {cli} has no default config file yet (never launched?) — open it once, then re-run agent-sync")
        else:
            env.log(f"mcp-gen: {cli} NOT aligned (best-effort, continuing)")
            healthy = False

    skipped: set[str] = set()
    if _process_running("claude"):
        # Deliberate: rewriting .claude.json under a running Claude can lose
        # whatever it flushes next. Recorded, because the drift this leaves
        # behind must not count against the run that chose to leave it.
        skipped.add("claude")
        env.log("mcp-gen: claude ACTIVE -> not touching .claude.json live (sentinel only)")
    else:
        r = _run_python_script([sys.executable, str(render_path), "--write", "claude"])
        _append_log(env, r.stdout, r.stderr)
        if r.returncode == 0:
            env.log("mcp-gen: claude aligned (was closed)")
        elif r.returncode == 3:
            env.log("mcp-gen: claude has no .claude.json yet (never launched?) — open Claude Code once, then re-run agent-sync")
        else:
            env.log("mcp-gen: claude not aligned (best-effort)")
            healthy = False

    # --json, not the human report: the verdict has to be able to say "this
    # CLI was skipped on purpose". The old code read one global counter off
    # the summary line, so drift belonging to a Claude this run deliberately
    # did not write still failed the phase -- and kept failing for as long as
    # Claude stayed open, which is most of the time.
    diag = _run_python_script([sys.executable, str(render_path), "--json"])
    if diag.returncode != 0:
        _append_log(env, diag.stdout, diag.stderr)
        env.log("mcp-gen: final drift diagnostic failed")
        return False
    try:
        report = json.loads(diag.stdout)
        per_cli = report["clis"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        _append_log(env, diag.stdout, diag.stderr)
        env.log(f"mcp-gen: could not read the drift report ({exc})")
        return False
    drift = sum(c.get("diff", 0) for name, c in per_cli.items() if name not in skipped)
    extra = sum(c.get("extra", 0) for c in per_cli.values())
    deferred = sum(per_cli[n].get("diff", 0) for n in skipped if n in per_cli)
    if deferred > 0:
        env.log(
            f"mcp-gen: {deferred} server(s) differ in the config of {', '.join(sorted(skipped))}, "
            "which this run left alone on purpose -- close it and the next run writes them"
        )
    if drift > 0:
        env.log(f"mcp-gen: SENTINEL — {drift} servers diverge from the manifest")
    if extra > 0:
        env.log(f"mcp-gen: NOTE — {extra} servers outside the manifest (kept as-is): register them in manifest.yaml to propagate them everywhere")
    # Drift notification: NOT here (single-megaphone rule). agent_sync stays
    # silent; the only alert surface is creds_health -> agent-doctor.
    return healthy and drift == 0


# ── 3. vault_skills ──────────────────────────────────────────────────────

def seed_starter_skills(env: Env) -> None:
    """Create skills.manifest.yaml from the shipped example on a fresh install.

    README advertises seven starter commands that "ship with the engine", and
    every one of them was vendored and tested -- but nothing ever created the
    manifest that turns them into runtime views. skills-sync printed "manifest
    not found ... skipping" to stderr and exited 0, INIT.md told the installing
    agent there were no base skills and to skip the step, and the doctor's only
    signal was a WARN worded as normal for a fresh install. The result: five
    cold installs where /vault-doctor and /nexgen-update simply did not exist,
    including for the user who most needed a one-word upgrade command.

    Deliberately narrow, so this stays a fresh-install courtesy and never an
    engine that overwrites user data:
      - only when the manifest is absent -- an existing one is never touched,
        so emptying it (`skills: {}`) is a permanent opt-out;
      - only when the bodies it declares actually resolve, each under the root
        its origin names: `origin: engine` resolves in the engine clone (that
        is the point of that origin, and it makes a split topology work instead
        of being a reason to skip), `origin: vault` under THIS data root.
    """
    target = env.instance_ul / "skills" / "skills.manifest.yaml"
    if target.exists():
        return
    example = env.ul / "skills" / "skills.manifest.yaml.example"
    if not example.is_file():
        return
    try:
        declared = (yaml.safe_load(example.read_text(encoding="utf-8")) or {}).get("skills") or {}
    except (OSError, yaml.YAMLError) as exc:
        env.log(f"skills: cannot read the shipped starter manifest ({exc}) — not seeding")
        return
    roots = {"vault": env.instance_ul / "skills", "engine": env.ul / "skills"}
    absent = sorted(
        name
        for name, spec in declared.items()
        if isinstance(spec, dict)
        and spec.get("origin") in roots
        and not (roots[spec["origin"]] / str(name) / "SKILL.md").is_file()
    )
    if absent:
        env.log(
            "skills: the engine's starter commands do not resolve where their origin says "
            f"({', '.join(absent)}) — not seeding a manifest that would point at nothing"
        )
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(example, target)
    except OSError as exc:
        env.log(f"skills: could not seed {target} ({exc})")
        return
    env.log(
        f"skills: seeded {target.name} with the {len(declared)} starter commands shipped by the "
        "engine (first install only — an existing manifest is never overwritten)"
    )


def vault_skills(env: Env) -> bool:
    """Reserve the two local views; skills-sync materializes their contents.

    Directly linking every Vault skill into ~/.agents/skills was the eager
    discovery bug: Codex enumerated the entire library before any task began.
    The dedicated synchronizer now owns library materialization and exposure.
    """
    seed_starter_skills(env)
    healthy = True
    for label, root in (("active skill view", env.active_skills), ("skill library", env.skill_library)):
        # A whole-root link was the original eager-discovery failure. Unlinking
        # the view is safe: it never removes the destination or any old bodies,
        # which the explicit legacy migration can quarantine afterwards.
        # _is_broken_whole_root_link() (not is_symlink(), not _is_link_like()):
        # is_symlink() misses Windows junctions entirely, and the generic
        # reparse-point test would also classify a OneDrive cloud placeholder
        # as "link-like" and remove a real directory. The broken-junction-only
        # predicate (is_dir() False) matches exactly the recoverable state this
        # phase exists to normalize: on a broken junction the mkdir below would
        # otherwise FileExistsError on the occupied reparse point and brick
        # every apply, the opposite of the documented intent.
        if _is_broken_whole_root_link(root):
            _remove_path(root)
            root.mkdir(parents=True, exist_ok=True)
            env.log(f"skills: converted {label} from a whole-root link to a real directory")
        elif not root.exists():
            root.mkdir(parents=True, exist_ok=True)
        elif not root.is_dir():
            env.log(f"skills: {label} is not a directory, leaving it untouched for manual repair")
            healthy = False
    return healthy


# ── 3.5 skills_index ─────────────────────────────────────────────────────

def skills_index(env: Env) -> bool:
    skills_sync = env.engine_scripts / "skills-sync.py"
    if not skills_sync.is_file():
        env.log(f"skills-manifest: missing synchronizer {skills_sync}")
        return False
    r = _run_python_script([sys.executable, str(skills_sync), "--apply"])
    if r.returncode == 0:
        summary = next((ln for ln in r.stdout.splitlines() if "Total:" in ln), "")
        summary = re.sub(r"\x1b\[[0-9;]*m", "", summary).strip()
        env.log(f"skills-manifest: apply ok ({summary})")
    else:
        env.log("skills-manifest: apply failed (best-effort, detail in the manual diff)")
        return False
    return True


# ── 4. runtimes ──────────────────────────────────────────────────────────
# Runtime directory hygiene. skills-sync.py alone owns the per-skill views.
# Claude may point at the non-discovered library because its native loader is
# lazy. Codex must never point at the library or active shared root wholesale.

def runtimes(env: Env) -> bool:
    healthy = True
    for cli, rel in (("claude", ".claude/skills"), ("codex", ".codex/skills")):
        rt = env.home / Path(rel)
        points_to_library = _points_to(rt, env.skill_library)
        # Claude's native loader may see the non-discovered library as a whole.
        # Every other whole-root link, including a broken one, is unsafe:
        # normalize it before skills-sync creates any per-skill view.
        if _is_link_like(rt) and not (cli == "claude" and points_to_library):
            _remove_path(rt)
            rt.mkdir(parents=True, exist_ok=True)
            env.log(f"runtime: {rt} was an eager whole-library link — converted to a real folder")
        elif not rt.exists():
            rt.mkdir(parents=True, exist_ok=True)
        elif not rt.is_dir():
            env.log(f"runtime: {rt} is not a directory, leaving it untouched for manual repair")
            healthy = False
    return healthy


# ── 4.5 claude_hooks ─────────────────────────────────────────────────────
# Pure-Python JSON merge instead of shelling out to jq (a real external
# dependency agent-sync.sh silently no-ops without): same behavior, one
# fewer thing that has to be installed on either OS.

def _claude_present(env: "Env") -> bool:
    """Best-effort 'Claude Code is installed on this host' probe, mirroring
    render.py's _antigravity_present(): runtimes() (above) unconditionally
    creates ~/.claude/skills -- hence ~/.claude itself -- regardless of
    whether Claude Code is installed, so that directory's mere existence is
    not a valid signal that Claude Code is here to use it. Left unfixed,
    this phase and claude_permissions() below would react to their OWN
    footprint: the exact "provisioner reacts to its own footprint" bug
    already corrected for Antigravity in mcp/render.py. Probe the product's
    own footprint instead: the CLI binary (agent-doctor.sh's own
    `command -v claude` convention) or the settings file Claude Code itself
    writes on first launch and this provisioner never creates."""
    if shutil.which("claude"):
        return True
    return (env.home / ".claude" / "settings.json").is_file()


def claude_hooks(env: Env) -> bool:
    hook_src = env.ul / "hooks" / "claude-vault-checkpoint.mjs"
    claude_dir = env.home / ".claude"
    if not hook_src.is_file() or not _claude_present(env):
        return True
    settings_path = claude_dir / "settings.json"
    try:
        validate_claude_settings(settings_path)
    except ConfigValidationError as exc:
        env.log(f"claude-hooks: settings preflight failed ({exc})")
        return False

    hook_dst = claude_dir / "claude-vault-checkpoint.mjs"
    src_bytes = hook_src.read_bytes()
    if not hook_dst.exists() or hook_dst.read_bytes() != src_bytes:
        hook_dst.write_bytes(src_bytes)
        env.log(f"claude-hooks: deployed {hook_dst}")

    if not settings_path.is_file():
        return True
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        env.log("claude-hooks: settings.json not valid JSON; skipping merge")
        return False
    if not isinstance(settings, dict):
        # Valid JSON but not an object (e.g. "[]") -- settings.setdefault
        # below would crash with AttributeError, aborting the rest of this
        # agent-sync run (publish/creds/health all run after this call in
        # main()). Found in a full-codebase audit, Gemini via agy, 2026-07-09.
        env.log("claude-hooks: settings.json root is not an object; skipping merge")
        return False

    command = f'node "{hook_dst}"'
    hooks = settings.setdefault("hooks", {})
    changed = False
    for event in ("SessionStart", "PreCompact"):
        entries = hooks.setdefault(event, [])
        present = any(h.get("command") == command for matcher in entries for h in matcher.get("hooks", []))
        if not present:
            entries.append({"hooks": [{"type": "command", "command": command, "timeout": 5}]})
            changed = True
    if not changed:
        return True
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(settings_path, settings_path.with_name(f"settings.json.pre-hooks-{stamp}.bak"))
    _atomic_write_text(settings_path, json.dumps(settings, indent=2) + "\n")
    env.log(f"claude-hooks: merged SessionStart/PreCompact into {settings_path}")
    return True


# ── 4.6 claude_permissions ───────────────────────────────────────────────
# The engine ships the MECHANISM only. The policy -- which posture, which
# guardrail -- is instance data in the private vault, so no end user ever
# inherits somebody else's permission choices: with no permissions manifest
# this phase is a complete no-op.
#
# Why a hook and not a `permissions.deny` list: under bypassPermissions the
# permission engine is skipped entirely, so deny rules may never be consulted.
# A PreToolUse hook still runs, which makes it the only guardrail that holds
# in the very posture that needs one.
#
# Despite the name (kept for phase-list/log-line/test compatibility instead
# of rippling a rename through every file that names this phase), this is the
# ONE permissions phase for every CLI the manifest declares a posture for, not
# just Claude. Claude keeps its own dedicated hooks+posture handling below,
# unchanged. Any other CLI in `posture:` is dispatched to a per-CLI renderer
# when this engine has a verified one (PERMISSION_RENDERERS in
# config_schema.py), or WARNED about and left unapplied when it doesn't. An
# unrenderable dialect must never fail this whole phase and take Claude's own
# posture down with it -- that is exactly the 2026-07-30 incident this
# generalization fixes ("posture.codex has unsupported CLI codex" refused the
# entire phase, so even Claude's own bypass+guardrail never got applied).

def _permissions_hook_command(hook_dst: Path) -> str:
    """Same shape claude_hooks() uses, so both phases produce byte-identical
    command strings and neither re-adds an entry the other already wrote."""
    return f'node "{hook_dst}"'


def _permissions_backup(path: Path) -> None:
    """Same `.pre-permissions-<timestamp>.bak` convention as the Claude
    settings.json backup below, extended to every other CLI's own config
    file: never overwrite an existing posture-bearing config without one."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, path.with_name(f"{path.name}.pre-permissions-{stamp}.bak"))


def _apply_claude_permissions(env: Env, manifest: dict, manifest_path: Path, claude_dir: Path) -> bool:
    """The original claude_permissions() body (unchanged logic/order), split
    out so the CLIs dispatched below can run independently of whether
    ~/.claude even exists on this host."""
    try:
        validate_claude_settings(claude_dir / "settings.json")
    except ConfigValidationError as exc:
        env.log(f"claude-permissions: refused ({exc})")
        return False

    # 1) Deploy every hook body targeting Claude, BEFORE touching settings.
    # Registering a hook whose file is missing would leave Claude invoking a
    # nonexistent script on every Bash call.
    deployed: list[tuple[dict, Path]] = []
    for spec in manifest.get("hooks", []) or []:
        if "claude" not in spec.get("targets", []):
            continue
        src = (manifest_path.parent / spec["file"]).resolve()
        # The validator already refused '..' and absolute paths; re-check the
        # resolved result in case a symlink inside the vault points outward.
        if not str(src).startswith(str(manifest_path.parent.resolve())):
            # "skipped" would understate this: returning False here aborts
            # the whole function, so no hook gets deployed and settings.json
            # never gets merged either, not just this one entry.
            env.log(
                f"claude-permissions: {spec['name']} resolves outside permissions/ "
                "-- refusing the whole hooks/settings phase for Claude, not just this entry"
            )
            return False
        if not src.is_file():
            env.log(f"claude-permissions: missing hook body {src}")
            return False
        dst = claude_dir / src.name
        body = src.read_bytes()
        if not dst.exists() or dst.read_bytes() != body:
            dst.write_bytes(body)
            env.log(f"claude-permissions: deployed {dst}")
        deployed.append((spec, dst))

    settings_path = claude_dir / "settings.json"
    if not settings_path.is_file():
        return True
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        env.log("claude-permissions: settings.json not valid JSON; skipping merge")
        return False
    if not isinstance(settings, dict):
        env.log("claude-permissions: settings.json root is not an object; skipping merge")
        return False

    before = json.dumps(settings, sort_keys=True)

    # 2) Hooks FIRST, posture second, and any anomaly refuses the whole phase.
    # Order matters and is the point: the posture is what turns the prompts
    # off, so it must never reach disk unless every declared guardrail is
    # registered. An earlier version logged and continued here, which could
    # write `bypassPermissions` while the guardrail stayed unregistered --
    # exactly the state this phase exists to prevent. Nothing is written until
    # both steps succeed, so returning early leaves settings.json untouched.
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        env.log("claude-permissions: refused, settings.hooks is not an object")
        return False
    for spec, dst in deployed:
        command = _permissions_hook_command(dst)
        entries = hooks.setdefault(spec["event"], [])
        if not isinstance(entries, list):
            env.log(f"claude-permissions: refused, settings.hooks.{spec['event']} is not a list")
            return False
        if any(h.get("command") == command for m in entries if isinstance(m, dict) for h in m.get("hooks", [])):
            continue
        entry: dict[str, object] = {
            "hooks": [{
                "type": "command",
                "command": command,
                "timeout": spec.get("timeout", 5),
            }],
        }
        if spec.get("matcher"):
            entry["matcher"] = spec["matcher"]
        entries.append(entry)

    # 3) Posture, only now that every declared guardrail is in place. Only the
    # one key is written: everything else in `permissions` (a user's own
    # allow/deny lists) is left untouched.
    posture = (manifest.get("posture") or {}).get("claude")
    if posture:
        perms = settings.setdefault("permissions", {})
        if not isinstance(perms, dict):
            env.log("claude-permissions: refused, settings.permissions is not an object")
            return False
        perms["defaultMode"] = PERMISSION_POSTURES[posture]
        if posture == "bypass":
            # Without this Claude blocks on an interactive confirmation
            # dialog at startup, which a background guard run cannot answer.
            settings["skipDangerousModePermissionPrompt"] = True

    if json.dumps(settings, sort_keys=True) == before:
        return True
    _permissions_backup(settings_path)
    _atomic_write_text(settings_path, json.dumps(settings, indent=2) + "\n")
    env.log(f"claude-permissions: applied posture/hooks into {settings_path}")
    return True


# ── Guardrail-hook adapters: OpenCode and Antigravity ───────────────────────
# Neither CLI's own hook contract matches Claude's (see the 2026-07-31 CLI
# permissions recon): OpenCode's `permission.ask` is a JS callback loaded
# in-process, not a spawned command; Antigravity's `hooks.json` command IS
# spawned, but with its own JSON shapes. Duplicating dangerous-command
# recognition per CLI was rejected by design. Instead each gets a THIN
# adapter -- this engine's own, public, mechanism-only code under
# agent-universal-layer/hooks/ -- that translates that CLI's native contract
# to/from the SAME stdin/stdout JSON shape a Claude PreToolUse guardrail body
# already speaks. The guardrail BODY itself -- the actual policy -- stays
# private vault data referenced by manifest.hooks[].file, exactly as it is
# for Claude; only the plumbing around it is new.
#
# Scope, deliberately narrow: only the PreToolUse-shaped, shell-command
# guardrail this feature exists for (see VERIFIED_HOOK_EVENTS and
# GUARDRAIL_SHELL_MATCHER in config_schema.py). A hook spec naming an
# unsupported event or a Claude tool matcher other than "Bash" for one of
# these two targets is refused for THAT target alone, never guessed at.
#
# Ordering mirrors Claude's own precedent, non-negotiable: install/register
# the guardrail FIRST, and only ever write that CLI's own bypass posture
# once the guardrail it was declared with is confirmed in place. See
# claude_permissions() below for how the three-way status returned here
# (see _GuardrailStatus) gates that CLI's posture render.

# "absent"       -- nothing declared for this CLI, or the CLI itself has
#                   never been launched: no guardrail to install, posture
#                   dispatch proceeds exactly as if this feature did not
#                   exist (matches every existing CLI-absent test).
# "unrenderable" -- a guardrail WAS declared, but with an event/matcher this
#                   engine has no verified adapter for. Warn and leave it
#                   uninstalled -- same "never guess a dialect" rule as an
#                   unverified posture value -- but the manifest asked for a
#                   guardrail here, so that CLI's OWN posture must not reach
#                   disk either. Does not fail the phase as a whole.
# "hard_fail"    -- the event/matcher WAS renderable, but something the
#                   engine cannot safely proceed past happened while
#                   installing it (missing hook body, a path escaping
#                   permissions/, a malformed CLI config). Refuses that
#                   CLI's posture AND fails the whole phase, matching
#                   Claude's own hard-refusal precedent for the same
#                   failure modes.
# "ok"           -- installed (or already up to date, unchanged, idempotent).


def _guardrail_specs_for(manifest: dict, cli: str) -> tuple[list[dict] | None, bool]:
    """Hook specs declared for `cli`, filtered through what this engine has a
    verified adapter for. Returns (specs, declared): declared is True iff at
    least one manifest hook names `cli` in targets; specs is None (with
    declared True) if any of those entries names an event or matcher outside
    VERIFIED_HOOK_EVENTS/GUARDRAIL_SHELL_MATCHER for this CLI -- refused as a
    whole for this target, never partially guessed at."""
    specs = [s for s in manifest.get("hooks", []) or [] if cli in s.get("targets", [])]
    if not specs:
        return [], False
    allowed_events = VERIFIED_HOOK_EVENTS.get(cli, frozenset())
    for spec in specs:
        if spec.get("event") not in allowed_events:
            return None, True
        matcher = spec.get("matcher")
        if matcher is not None and matcher != GUARDRAIL_SHELL_MATCHER:
            return None, True
    return specs, True


def _log_matcher_scope_gap(specs: list[dict], cli: str, env: Env) -> None:
    """Say out loud that an unscoped guardrail covers less here than on Claude.

    An absent matcher means "every tool" in Claude's own hook contract, and a
    manifest author who writes one hook for three targets reasonably reads it
    that way. But these adapters can only see shell commands: that is the one
    decision-bearing hook each of these CLIs exposes. Same declaration, two
    different coverages -- which is fine, since the shell is the surface that
    matters, but it must never be silent."""
    unscoped = [s.get("name", "?") for s in specs if s.get("matcher") is None]
    if unscoped:
        env.log(
            f"claude-permissions: NOTE {cli} guardrail hook(s) {', '.join(unscoped)} declare no "
            f"matcher, which on Claude covers every tool; the {cli} adapter can only see shell "
            "commands, so coverage here is shell-only"
        )


def _deploy_guardrail_bodies(
    specs: list[dict], manifest_path: Path, dest_dir: Path, env: Env, *, label: str
) -> list[dict] | None:
    """Copy each declared guardrail body into dest_dir, mirroring Claude's own
    hook-body deployment in _apply_claude_permissions (same traversal check,
    same "only rewrite if bytes differ" idempotence). Returns the sidecar
    hook list [{file, timeout}, ...] an adapter reads at call time, or None
    (refusing the whole install for this CLI) if a body is missing or
    resolves outside the permissions directory."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for spec in specs:
        src = (manifest_path.parent / spec["file"]).resolve()
        if not str(src).startswith(str(manifest_path.parent.resolve())):
            env.log(f"claude-permissions: {label} {spec['name']} resolves outside permissions/ -- refusing")
            return None
        if not src.is_file():
            env.log(f"claude-permissions: {label} missing hook body {src}")
            return None
        dst = dest_dir / src.name
        body = src.read_bytes()
        if not dst.exists() or dst.read_bytes() != body:
            dst.write_bytes(body)
            env.log(f"claude-permissions: {label} deployed {dst}")
        out.append({"file": str(dst), "timeout": spec.get("timeout", 5)})
    return out


def _deploy_static_adapter(src: Path, dst: Path, env: Env, *, label: str) -> bool:
    """Copy an engine-owned adapter script verbatim (byte-identical across
    installs -- see the .mjs files themselves), only rewriting if changed."""
    if not src.is_file():
        env.log(f"claude-permissions: {label} missing engine adapter {src}")
        return False
    body = src.read_bytes()
    if not dst.exists() or dst.read_bytes() != body:
        dst.write_bytes(body)
        env.log(f"claude-permissions: {label} deployed {dst}")
    return True


def _write_guardrail_sidecar(path: Path, hooks_list: list[dict]) -> bool:
    """The per-hook config an adapter reads fresh on every call. Returns
    True iff the file's content changed (for logging only, callers decide
    what to log with the label they own)."""
    content = json.dumps({"hooks": hooks_list}, indent=2) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    _atomic_write_text(path, content)
    return True


def _register_opencode_plugin(env: Env, config_path: Path, adapter_dst: Path) -> bool:
    """Add the guardrail plugin to OpenCode's own `"plugin"` array -- append
    only, dedup by value, every other entry (the user's own plugins)
    untouched. `file://` is the documented local-plugin form (see the recon)."""
    raw = config_path.read_text(encoding="utf-8")
    try:
        config = parse_jsonc(raw) if config_path.suffix == ".jsonc" else json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        env.log(f"claude-permissions: opencode-guardrail: refused ({config_path.name} is not valid JSON/JSONC: {exc})")
        return False
    if not isinstance(config, dict):
        env.log(f"claude-permissions: opencode-guardrail: refused ({config_path.name} root is not an object)")
        return False
    plugins = config.get("plugin", [])
    if not isinstance(plugins, list):
        env.log(f"claude-permissions: opencode-guardrail: refused ({config_path.name} 'plugin' is not a list)")
        return False
    # `file://` is the documented local-plugin form (see the recon), but the
    # canonical spelling on Windows needs the third slash and forward slashes:
    # `file:///C:/Users/<name>/...` -- `file://C:\...` parses with host "C:" and
    # backslash path characters, so the plugin would not load while the
    # bypass posture was still applied, unguarded with no failure signal
    # (2026-08-15 review). Path.as_uri() emits the canonical form on every
    # platform; a relative path can never be a valid file URI. resolve() is
    # wrapped so a network share going away degrades to the function's
    # usual log-and-False instead of crashing the whole phase (2026-08-15
    # council-7, Opus 5).
    try:
        entry = adapter_dst.resolve().as_uri()
    except (OSError, ValueError) as exc:
        env.log(f"claude-permissions: opencode-guardrail: cannot resolve {adapter_dst} ({exc}) -- plugin left unregistered")
        return False

    def _normalize_plugin(value: object) -> str | None:
        # Dedup and legacy migration (2026-08-15 council, Opus 5): an
        # existing install may carry the OLD spelling of this very entry
        # (pre-fix `file://` + backslash path on Windows), so a plain string
        # equality against `entry` would append a duplicate every run.
        # Compare by resolved path instead: strip a file:// prefix, then
        # resolve() both sides. Only file:// entries and absolute paths are
        # compared: a bare npm package name or a URL must not be resolved
        # against the current working directory of the agent-sync process
        # (2026-08-15 council-7, Opus 5). On Windows the canonical file URI
        # has a third slash before the drive letter (`file:///C:/...`):
        # after the prefix strip that reads `/C:/...`, which pathlib would
        # resolve against the CURRENT drive -- strip that leading slash for
        # drive paths so the canonical form dedups against itself
        # (2026-08-15 council-2, Opus 5).
        if not isinstance(value, str):
            return None
        text = value.strip()
        if text.startswith("file://"):
            text = text[len("file://"):]
        elif not text.startswith(("/", "\\\\")):
            if not (IS_WINDOWS and len(text) > 1 and text[1] == ":"):
                return None  # not a file URI, an absolute path, or a drive path
        if IS_WINDOWS and text.startswith("/") and len(text) > 2 and text[2] == ":":
            text = text[1:]
        try:
            return Path(text).resolve().as_uri()
        except (OSError, ValueError):
            return None

    normalized = []
    canonical_count = 0
    removed_legacy = 0
    for item in plugins:
        if isinstance(item, str) and item.strip() == entry:
            # Deduplicate repeated canonical entries too: a config dirtied
            # by earlier buggy runs must converge to a single entry, not
            # keep N identical copies (2026-08-15 council-7, Opus 5).
            canonical_count += 1
            continue
        ident = _normalize_plugin(item)
        if ident is not None and ident == entry:
            # Legacy spelling of this very entry (pre-fix `file://` +
            # backslash path on Windows): must be MIGRATED to the canonical
            # form, not treated as "already present" -- a Windows install
            # that only carries the old spelling would otherwise keep a
            # plugin entry opencode cannot load, forever (2026-08-15
            # council-4, Opus 5).
            removed_legacy += 1
            continue
        normalized.append(item)
    if canonical_count == 1 and removed_legacy == 0:
        return True  # exactly one canonical entry, nothing stale to migrate
    updated_plugins = [*normalized, entry]
    _permissions_backup(config_path)
    if config_path.suffix == ".jsonc":
        updated = set_jsonc_top_level_value(raw, "plugin", updated_plugins)
    else:
        config["plugin"] = updated_plugins
        updated = json.dumps(config, indent=2) + "\n"
    _atomic_write_text(config_path, updated)
    env.log(f"claude-permissions: opencode-guardrail: registered plugin in {config_path}")
    return True


def _install_opencode_guardrail(env: Env, manifest: dict, manifest_path: Path) -> str:
    """Install the OpenCode guardrail plugin + its sidecar config. Returns one
    of "absent" / "unrenderable" / "hard_fail" / "ok" (see the block comment
    above) -- claude_permissions() uses this to gate opencode's own posture."""
    specs, declared = _guardrail_specs_for(manifest, "opencode")
    if not declared:
        return "absent"
    if specs is None:
        known = ", ".join(sorted(VERIFIED_HOOK_EVENTS.get("opencode", frozenset())))
        env.log(
            "claude-permissions: WARNING opencode guardrail hook declared with an event/matcher "
            f"this engine cannot render (verified event(s): {known}; matcher must be unset or "
            f"'{GUARDRAIL_SHELL_MATCHER}') -- opencode guardrail stays UNINSTALLED, guessing a "
            "translation is not safe"
        )
        return "unrenderable"
    _log_matcher_scope_gap(specs, "opencode", env)

    config_path = _opencode_config_path(env.home)
    if not config_path.is_file():
        env.log(f"claude-permissions: {config_path} not present (OpenCode never launched yet) -- opencode guardrail left uninstalled")
        return "absent"

    plugin_dir = config_path.parent
    hooks_list = _deploy_guardrail_bodies(
        specs, manifest_path, plugin_dir / "nexgen-guardrail-hooks", env, label="opencode-guardrail:"
    )
    if hooks_list is None:
        return "hard_fail"

    adapter_src = env.ul / "hooks" / "opencode-guardrail-plugin.mjs"
    adapter_dst = plugin_dir / "nexgen-guardrail-plugin.mjs"
    if not _deploy_static_adapter(adapter_src, adapter_dst, env, label="opencode-guardrail:"):
        return "hard_fail"

    if _write_guardrail_sidecar(plugin_dir / "nexgen-guardrail.config.json", hooks_list):
        env.log(f"claude-permissions: opencode-guardrail: wrote {plugin_dir / 'nexgen-guardrail.config.json'}")

    if not _register_opencode_plugin(env, config_path, adapter_dst):
        return "hard_fail"
    return "ok"


def _install_antigravity_guardrail(env: Env, manifest: dict, manifest_path: Path) -> str:
    """Install the Antigravity guardrail adapter + register it in the CLI's
    own global `~/.gemini/config/hooks.json`. Returns one of "absent" /
    "unrenderable" / "hard_fail" / "ok" (see the block comment above)."""
    specs, declared = _guardrail_specs_for(manifest, "antigravity")
    if not declared:
        return "absent"
    if specs is None:
        known = ", ".join(sorted(VERIFIED_HOOK_EVENTS.get("antigravity", frozenset())))
        env.log(
            "claude-permissions: WARNING antigravity guardrail hook declared with an event/matcher "
            f"this engine cannot render (verified event(s): {known}; matcher must be unset or "
            f"'{GUARDRAIL_SHELL_MATCHER}') -- antigravity guardrail stays UNINSTALLED, guessing a "
            "translation is not safe"
        )
        return "unrenderable"
    _log_matcher_scope_gap(specs, "antigravity", env)

    # Same presence signal _apply_antigravity_posture already uses: the
    # CLI's own settings.json existing is this engine's one confirmed proxy
    # for "Antigravity has actually been launched here" (see the recon on
    # why ~/.gemini alone is not a safe proxy).
    settings_path = env.home / ".gemini" / "antigravity-cli" / "settings.json"
    if not settings_path.is_file():
        env.log(f"claude-permissions: {settings_path} not present (Antigravity never launched yet) -- antigravity guardrail left uninstalled")
        return "absent"

    hooks_path = env.home / ".gemini" / "config" / "hooks.json"
    adapter_dir = hooks_path.parent
    hooks_list = _deploy_guardrail_bodies(
        specs, manifest_path, adapter_dir / "nexgen-guardrail-hooks", env, label="antigravity-guardrail:"
    )
    if hooks_list is None:
        return "hard_fail"

    adapter_src = env.ul / "hooks" / "antigravity-guardrail-adapter.mjs"
    adapter_dst = adapter_dir / "nexgen-guardrail-adapter.mjs"
    if not _deploy_static_adapter(adapter_src, adapter_dst, env, label="antigravity-guardrail:"):
        return "hard_fail"

    if _write_guardrail_sidecar(adapter_dir / "nexgen-guardrail.config.json", hooks_list):
        env.log(f"claude-permissions: antigravity-guardrail: wrote {adapter_dir / 'nexgen-guardrail.config.json'}")

    # Antigravity's own hook-level timeout kills the WHOLE command; the
    # adapter separately enforces each body's own timeout via its own
    # subprocess call. Sum + a fixed buffer keeps the outer kill from firing
    # before the adapter's own per-body timeouts get a chance to.
    outer_timeout = sum(h["timeout"] for h in hooks_list) + 5
    desired_entry = {
        "enabled": True,
        "PreToolUse": [{
            "matcher": "run_command",
            "hooks": [{
                "type": "command",
                "command": f'node "{adapter_dst}"',
                "timeout": outer_timeout,
            }],
        }],
    }

    current: dict = {}
    if hooks_path.is_file():
        raw = hooks_path.read_text(encoding="utf-8")
        try:
            current = json.loads(raw)
        except json.JSONDecodeError as exc:
            env.log(f"claude-permissions: antigravity-guardrail: refused ({hooks_path.name} is not valid JSON: {exc})")
            return "hard_fail"
        if not isinstance(current, dict):
            env.log(f"claude-permissions: antigravity-guardrail: refused ({hooks_path.name} root is not an object)")
            return "hard_fail"

    # Own key only: every OTHER top-level hook name in this file -- whether
    # the user's or another tool's -- is preserved exactly, never removed.
    if current.get("nexgen-guardrail") == desired_entry:
        return "ok"
    if hooks_path.is_file():
        _permissions_backup(hooks_path)
    else:
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
    updated = {**current, "nexgen-guardrail": desired_entry}
    _atomic_write_text(hooks_path, json.dumps(updated, indent=2) + "\n")
    env.log(f"claude-permissions: antigravity-guardrail: registered PreToolUse guardrail in {hooks_path}")
    return "ok"


def _apply_codex_posture(env: Env, value: str) -> bool:
    """Codex's own bypass dialect: approval_policy/sandbox_mode, root-level
    TOML keys (verified live + against the installed binary's one-shot
    bypass flag). Only 'bypass' has a verified renderer -- PERMISSION_RENDERERS
    keeps any other value from ever reaching this function."""
    path = env.home / ".codex" / "config.toml"
    if not path.is_file():
        env.log("claude-permissions: ~/.codex/config.toml not present (Codex never launched yet) -- codex posture left unapplied")
        return True
    if not toml_reader_available():
        env.log(
            "claude-permissions: WARNING no TOML reader available on this Python runtime "
            "(Python 3.11+, or Python 3.10 with 'tomli' installed) -- codex posture stays "
            "UNAPPLIED (nothing was written for it)"
        )
        return True
    raw = path.read_text(encoding="utf-8")
    try:
        current = parse_toml(raw)
    except ConfigValidationError as exc:
        env.log(f"claude-permissions: refused codex posture ({exc})")
        return False
    desired = CODEX_POSTURE_RENDER[value]
    if all(current.get(k) == v for k, v in desired.items()):
        return True
    text = raw
    try:
        for key, val in desired.items():
            text = set_toml_root_string(text, key, val)
    except (ValueError, ConfigValidationError) as exc:
        env.log(f"claude-permissions: refused codex posture write ({exc})")
        return False
    _permissions_backup(path)
    _atomic_write_text(path, text)
    env.log(f"claude-permissions: applied codex posture '{value}' into {path}")
    return True


def _apply_opencode_posture(env: Env, value: str) -> bool:
    """OpenCode's own dialect: permission.edit / permission.bash (verified
    against the installed @opencode-ai/sdk type definitions). Only edit/bash
    are touched -- every other key in the config, including other
    permission.* dimensions, is left exactly as the user had it."""
    path = _opencode_config_path(env.home)
    if not path.is_file():
        env.log(f"claude-permissions: {path} not present (OpenCode never launched yet) -- opencode posture left unapplied")
        return True
    raw = path.read_text(encoding="utf-8")
    try:
        config = parse_jsonc(raw) if path.suffix == ".jsonc" else json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        env.log(f"claude-permissions: refused opencode posture ({path.name} is not valid JSON/JSONC: {exc})")
        return False
    if not isinstance(config, dict):
        env.log(f"claude-permissions: refused opencode posture ({path.name} root is not an object)")
        return False
    current_permission = config.get("permission")
    if current_permission is not None and not isinstance(current_permission, dict):
        env.log(f"claude-permissions: refused opencode posture ({path.name} 'permission' is not an object)")
        return False
    desired = OPENCODE_POSTURE_RENDER[value]
    permission = dict(current_permission or {})
    if all(permission.get(k) == v for k, v in desired.items()):
        return True
    permission.update(desired)
    _permissions_backup(path)
    if path.suffix == ".jsonc":
        updated = set_jsonc_top_level_value(raw, "permission", permission)
    else:
        config["permission"] = permission
        updated = json.dumps(config, indent=2) + "\n"
    _atomic_write_text(path, updated)
    env.log(f"claude-permissions: applied opencode posture '{value}' into {path}")
    return True


def _apply_antigravity_posture(env: Env, value: str) -> bool:
    """Antigravity's own dialect: toolPermission (shell) + artifactReviewPolicy
    (file edits) in the CLI's own settings.json (verified against the
    installed binary's embedded reference documentation). Both keys are
    written together -- setting only toolPermission would leave file edits
    still gated, a half-bypass that looks applied and isn't."""
    path = env.home / ".gemini" / "antigravity-cli" / "settings.json"
    if not path.is_file():
        env.log(f"claude-permissions: {path} not present (Antigravity never launched yet) -- antigravity posture left unapplied")
        return True
    raw = path.read_text(encoding="utf-8")
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        env.log(f"claude-permissions: refused antigravity posture ({path.name} is not valid JSON: {exc})")
        return False
    if not isinstance(config, dict):
        env.log(f"claude-permissions: refused antigravity posture ({path.name} root is not an object)")
        return False
    desired = ANTIGRAVITY_POSTURE_RENDER[value]
    if all(config.get(k) == v for k, v in desired.items()):
        return True
    config.update(desired)
    _permissions_backup(path)
    _atomic_write_text(path, json.dumps(config, indent=2) + "\n")
    env.log(f"claude-permissions: applied antigravity posture '{value}' into {path}")
    return True


# CLI name -> renderer(env, value). Deliberately excludes "claude" (handled by
# _apply_claude_permissions above, which also owns the hooks it shares
# settings.json with). A CLI absent here has no renderer at all, regardless
# of what PERMISSION_RENDERERS says -- the dispatcher below checks both.
_CLI_POSTURE_RENDERERS: dict[str, Callable[["Env", str], bool]] = {
    "codex": _apply_codex_posture,
    "opencode": _apply_opencode_posture,
    "antigravity": _apply_antigravity_posture,
}


def claude_permissions(env: Env) -> bool:
    manifest_path = env.instance_ul / "permissions" / "manifest.yaml"
    if not manifest_path.is_file():
        return True
    try:
        manifest = validate_permissions_manifest(
            yaml.safe_load(manifest_path.read_text(encoding="utf-8")), manifest_path
        )
    except ConfigValidationError as exc:
        env.log(f"claude-permissions: refused ({exc})")
        return False
    except (OSError, yaml.YAMLError) as exc:
        env.log(f"claude-permissions: cannot read manifest ({exc})")
        return False

    posture = manifest.get("posture") or {}
    hook_targets = {t for spec in (manifest.get("hooks") or []) for t in spec.get("targets", [])}

    def warn_if_bare_bypass(cli: str) -> None:
        # A CLI that ends up in bypass with no guardrail hook declared for it
        # runs with no net: bypassPermissions-equivalent postures skip the
        # normal permission engine entirely, so only a PreToolUse-style hook
        # can still veto a command -- and today only Claude has one wired.
        # Applying bypass anyway is the documented, explicit will of whoever
        # owns this manifest; staying silent about the missing net is not.
        if posture.get(cli) == "bypass" and cli not in hook_targets:
            env.log(
                f"claude-permissions: WARNING {cli} posture is 'bypass' with NO guardrail hook "
                f"declared for {cli} -- {cli} is running WITHOUT A NET (nothing can veto a "
                "dangerous command before it runs)"
            )

    ok = True
    claude_dir = env.home / ".claude"
    if _claude_present(env):
        if _apply_claude_permissions(env, manifest, manifest_path, claude_dir):
            warn_if_bare_bypass("claude")
        else:
            ok = False
    # else: Claude Code isn't installed on this host -- nothing to apply for
    # it, but every other CLI below is independent of ~/.claude existing.

    # Guardrail hooks FIRST, exactly like Claude's own hooks-then-posture
    # order above -- and for the same reason: a posture that removes prompts
    # must never reach disk without the guardrail it was declared with. See
    # the block comment above _install_opencode_guardrail for what each of
    # the four status strings means and why only "hard_fail" fails this
    # whole phase while "unrenderable" only blocks that one CLI's posture.
    guard_status = {
        "opencode": _install_opencode_guardrail(env, manifest, manifest_path),
        "antigravity": _install_antigravity_guardrail(env, manifest, manifest_path),
    }
    if guard_status["opencode"] == "hard_fail" or guard_status["antigravity"] == "hard_fail":
        ok = False

    for cli, value in posture.items():
        if cli == "claude":
            continue
        status = guard_status.get(cli)
        if status == "hard_fail":
            env.log(
                f"claude-permissions: refused {cli} posture -- its declared guardrail hook failed "
                "to install (see the error above); a posture that removes prompts must never reach "
                "disk without the guardrail it was declared with"
            )
            ok = False
            continue
        if status == "unrenderable":
            env.log(
                f"claude-permissions: {cli} posture stays UNAPPLIED -- its declared guardrail hook "
                "has no verified renderer for this engine (see the warning above)"
            )
            continue
        renderer = _CLI_POSTURE_RENDERERS.get(cli)
        verified_values = PERMISSION_RENDERERS.get(cli, frozenset())
        if renderer is None or value not in verified_values:
            known = ", ".join(
                f"{c}:{'/'.join(sorted(values))}" for c, values in sorted(PERMISSION_RENDERERS.items())
            )
            env.log(
                f"claude-permissions: WARNING no verified renderer for CLI '{cli}' posture "
                f"'{value}' in this engine (verified: {known}) -- '{cli}' posture stays "
                "UNAPPLIED (nothing was written for it); guessing a dialect is not safe"
            )
            continue
        if renderer(env, value):
            warn_if_bare_bypass(cli)
        else:
            ok = False

    return ok


# ── 5. publish ───────────────────────────────────────────────────────────

def publish(env: Env) -> bool:
    if env.remote in ("local", "none"):
        env.log("push: skipped (Local-Only mode)")
        return True
    if _git(env, "remote", "get-url", env.remote).returncode != 0:
        env.log(f"push: authoritative remote {env.remote} is not configured")
        return False
    if _git(env, "fetch", "--prune", env.remote, env.branch).returncode != 0:
        env.log(f"push: {env.remote} unreachable — no publication attempted")
        return False

    lh = _git(env, "rev-parse", env.branch)
    rh = _git(env, "rev-parse", f"{env.remote}/{env.branch}")
    mb = _git(env, "merge-base", env.branch, f"{env.remote}/{env.branch}")
    if lh.returncode or rh.returncode or mb.returncode:
        env.log(f"push: cannot compare local branch with {env.remote}/{env.branch}")
        return False
    lh, rh, mb = lh.stdout.strip(), rh.stdout.strip(), mb.stdout.strip()

    if lh == rh:
        env.log(f"push: authoritative {env.remote}/{env.branch} already aligned")
    elif mb == rh:
        ahead_r = _git(env, "rev-list", "--count", f"{env.remote}/{env.branch}..{env.branch}")
        ahead = ahead_r.stdout.strip() or "?"
        if _git(env, "push", env.remote, env.branch).returncode != 0:
            env.log(f"push: authoritative publication to {env.remote} failed")
            return False
        env.log(f"push: {ahead} commit(s) published to {env.remote}")
    elif mb == lh:
        env.log(f"push: BLOCKED because local {env.branch} is behind authoritative {env.remote}/{env.branch}")
        return False
    else:
        env.log(f"push: BLOCKED because local {env.branch} diverged from authoritative {env.remote}/{env.branch}")
        return False

    for mirror in env.mirrors:
        if _git(env, "push", mirror, env.branch).returncode == 0:
            env.log(f"push: mirror {mirror} aligned")
            continue
        if (_git(env, "fetch", "--prune", mirror, env.branch).returncode == 0
                and _git(env, "push", "--force-with-lease", mirror, env.branch).returncode == 0):
            env.log(f"push: mirror {mirror} realigned to the authoritative line (force-with-lease)")
        else:
            # The authoritative publish succeeded. A mirror outage is
            # observable debt, not grounds to call the canonical write lost.
            env.log(f"push: mirror {mirror} unreachable or lease expired — authoritative remote is safe")
    return True


# ── 6. creds_health ──────────────────────────────────────────────────────
# Unifies agent-sync.sh's external agent-healthcheck.sh call and
# agent-sync.ps1's inline Send-Healthcheck: same debounce state file, same
# interval, same doctor-summary/Telegram/webhook contract, one
# implementation for both OSes instead of a shell script + a PS function.

_FAIL_RE = re.compile(r"FAIL=([1-9]\d*)")


def _load_env_conf(env: Env) -> None:
    conf = env.home / ".config" / "environment.d" / "91-telegram-alert.conf"
    if not conf.is_file():
        return
    for line in conf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


def _ensure_alert_creds(env: Env) -> None:
    if env.remote in ("local", "none"):
        return
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        return
    cred_id = os.environ.get("N8N_TELEGRAM_CRED_ID")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    remote_alias = os.environ.get("REMOTE_ALIAS")
    container = os.environ.get("N8N_CONTAINER", "n8n-n8n-1")
    if not (cred_id and chat and remote_alias):
        env.log("alert-creds: n8n source not configured (N8N_TELEGRAM_CRED_ID / TELEGRAM_CHAT_ID / REMOTE_ALIAS) — skipping, using env-based alerts if present")
        return
    remote_script = (
        "set -eu\n"
        "cred_id=$1\n"
        "tmpfile=$(mktemp /tmp/agent-sync-n8n-creds.XXXXXX)\n"
        "trap 'rm -f \"$tmpfile\"' EXIT HUP INT TERM\n"
        "chmod 600 \"$tmpfile\"\n"
        "n8n export:credentials --all --decrypted --output=\"$tmpfile\" >/dev/null 2>&1\n"
        "CRED_FILE=\"$tmpfile\" N8N_TELEGRAM_CRED_ID=\"$cred_id\" "
        "node -e 'const d=require(process.env.CRED_FILE);"
        "const list=Array.isArray(d)?d:[];"
        "const c=list.find(x=>x&&x.id===process.env.N8N_TELEGRAM_CRED_ID);"
        "process.stdout.write((c&&c.data&&(c.data.accessToken||c.data.token))||\"\")' 2>/dev/null\n"
    )
    token = ""
    for attempt in range(3):
        try:
            # shlex.quote(container): N8N_CONTAINER is attacker-controllable
            # by anything that can set a local env var before this runs (a
            # compromised dependency, a malicious skill/MCP server) -- an
            # unquoted value becomes a command the remote shell parses,
            # turning a local env-var write into arbitrary root execution on
            # the remote host via sudo. Found in a full-codebase audit,
            # Gemini via agy, 2026-07-09.
            r = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", remote_alias,
                 f"sudo -n docker exec -i {shlex.quote(container)} sh -s -- {shlex.quote(cred_id)}"],
                input=remote_script, capture_output=True, text=True, timeout=20,
            )
            token = r.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            token = ""
        if token or attempt == 2:
            break
        time.sleep(4)
    if not token:
        env.log("alert-creds: Telegram provisioning did NOT succeed after 3 attempts (remote unreachable or cred not retrieved) — will retry on the next agent-sync")
        return
    os.environ["TELEGRAM_BOT_TOKEN"] = token
    os.environ["TELEGRAM_CHAT_ID"] = chat
    env.log("alert-creds: Telegram provisioning from n8n completed")


def _doctor_summary(env: Env, timeout: int, *, strict: bool = False) -> str | None:
    if not IS_WINDOWS:
        doctor = env.engine_scripts / "agent-doctor.sh"
        if not doctor.is_file():
            return None
        cmd = ["bash", str(doctor), "--summary"]
        if strict:
            cmd.append("--strict")
    else:
        doctor = env.engine_scripts / "agent-doctor.ps1"
        if not doctor.is_file():
            return None
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(doctor), "-Summary"]
        if strict:
            cmd.append("-Strict")
    doctor_env = os.environ.copy()
    if doctor_env.get("NEXGEN_DISABLE_HOST_MUTATIONS") == "1":
        # Provisioning tests redirect HOME but can still inherit real CLI
        # binaries through PATH. Antigravity and OpenCode both create runtime
        # state even for their nominally read-only consumer probes, so a
        # sandboxed apply must keep the structural strict checks while
        # explicitly suppressing those live processes.
        doctor_env["NEXGEN_SKIP_LIVE_CONSUMER_PROBES"] = "1"
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=doctor_env,
        )
    except subprocess.TimeoutExpired:
        env.log(f"healthcheck: skipped (agent-doctor timeout after {timeout}s)")
        return None
    lines = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else None


def _localize_alert(env: Env, msg: str) -> str:
    """The engine's own strings are English-only, deliberately: this is a
    public repo, and mixing languages in the SOURCE is worse than being
    all-English. Translation is the user's own concern, done in their DATA,
    never hardcoded here. If vault_data/03-INFRA/alert-translate.sh exists
    and is executable, it gets the English message on stdin and its stdout
    (if non-empty) replaces it; any failure (missing, not executable,
    non-zero exit, timeout, empty output) falls back to the English
    original — a broken translator must never swallow a real alert."""
    data_dir = env.vault_data / "03-INFRA"
    commands: list[list[str]] = []
    if IS_WINDOWS:
        ps_translator = data_dir / "alert-translate.ps1"
        if ps_translator.is_file():
            commands.append([
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(ps_translator),
            ])
        cmd_translator = data_dir / "alert-translate.bat"
        if cmd_translator.is_file():
            commands.append(["cmd.exe", "/d", "/c", str(cmd_translator)])
    translator = data_dir / "alert-translate.sh"
    if translator.is_file() and (not IS_WINDOWS or os.access(translator, os.X_OK)):
        commands.append(["bash", str(translator)])
    for command in commands:
        try:
            r = subprocess.run(command, input=msg, capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
    return msg


def _send_healthcheck(env: Env) -> None:
    timeout = int(os.environ.get("AGENT_DOCTOR_TIMEOUT_SECONDS") or 20)
    summary = _doctor_summary(env, timeout=timeout)
    if not summary:
        return
    problem = bool(_FAIL_RE.search(summary))
    sig = "".join(summary.split())
    state_file = env.log_dir / "agent-healthcheck.state"
    interval = int(os.environ.get("AGENT_HEALTHCHECK_INTERVAL") or 86400)
    now = int(time.time())
    last, last_sig = 0, ""
    if state_file.is_file():
        lines = state_file.read_text(encoding="utf-8").splitlines()
        if lines and lines[0].isdigit():
            last = int(lines[0])
        if len(lines) >= 2:
            last_sig = lines[1]

    if not problem:
        _atomic_write_text(state_file, f"{now}\nok\n")
        return

    send = sig != last_sig or (now - last) >= interval
    if not send:
        return

    if _deliver_alert(env, summary):
        env.log(f"healthcheck: sent ({sig})")
    else:
        env.log(f"healthcheck: {summary} (no transport configured)")
    _atomic_write_text(state_file, f"{now}\n{sig}\n")


AUTO_UPGRADE_DEFAULT = "patch"


def _upgrade_step(current: str, target: str) -> str:
    """'patch', 'minor' or 'major' for the jump between two vX.Y.Z strings."""
    def parts(v: str) -> tuple[int, int, int]:
        nums = [int(n) for n in re.findall(r"\d+", v)[:3]]
        while len(nums) < 3:
            nums.append(0)
        return tuple(nums)  # type: ignore[return-value]
    a, b = parts(current), parts(target)
    if b[0] != a[0]:
        return "major"
    if b[1] != a[1]:
        return "minor"
    return "patch"


def _auto_upgrade(env: Env) -> None:
    """Take a released engine upgrade without asking, and say nothing about it.

    Updating itself is the guardian's job, not news: a notification for routine
    maintenance is how people learn to dismiss notifications. It speaks only
    when it CANNOT do the work.

    The three brakes are the updater's own and are not reimplemented here: it
    verifies the release commit signature, refuses to run against a dirty
    engine or data repository, and only ever offers a released tag -- which
    exists only after the full CI matrix went green on the merge. This adds one
    more: how far a jump may be taken unattended, `patch` by default, because a
    machine that upgrades its own minor versions overnight is a machine whose
    behaviour changed without anyone choosing it.

    Runs from the heartbeat, never from the guard: the updater's provisioning
    pass calls `agent-sync apply`, and the guard is holding the host-wide lock
    that pass would wait on.
    """
    level = (os.environ.get("AGENT_AUTO_UPGRADE") or AUTO_UPGRADE_DEFAULT).strip().lower()
    if level in {"off", "no", "0", "false"}:
        return
    updater = env.home / ".local" / "bin" / "nexgen-update"
    if not updater.exists():
        return
    check = _run_external([str(updater), "--check"], timeout=180, capture_output=True, text=True)
    if check.returncode != 0:
        _deliver_alert(env, "FAIL could not check for an engine update. "
                            "Run: nexgen-update --check")
        return
    current = target = ""
    for line in check.stdout.splitlines():
        if line.startswith("Current:"):
            current = line.split(":", 1)[1].strip()
        elif line.startswith("Latest released target:"):
            target = line.split(":", 1)[1].strip()
    if not current or not target or current.lstrip("v") == target.lstrip("v"):
        return
    step = _upgrade_step(current, target)
    allowed = {"patch": {"patch"}, "minor": {"patch", "minor"},
               "all": {"patch", "minor", "major"}}.get(level, {"patch"})
    if step not in allowed:
        env.log(f"auto-upgrade: {current} -> {target} is a {step} step, above AGENT_AUTO_UPGRADE={level}")
        return
    env.log(f"auto-upgrade: taking {current} -> {target} ({step})")
    run = _run_external([str(updater), "--yes"], timeout=1800, capture_output=True, text=True)
    if run.returncode == 0:
        env.log(f"auto-upgrade: now on {target}")
        return
    _deliver_alert(env, f"FAIL the engine could not update itself to {target}. "
                        f"Run: nexgen-update --check")


def _heartbeat_cli(argv: list[str]) -> int:
    """The independent maintenance beat: check the sync is still alive, then
    take any released upgrade. Both silent unless they cannot be done."""
    del argv
    _notify_stale_cli([])
    try:
        _auto_upgrade(Env())
    except Exception as exc:  # a broken upgrade attempt must not kill the beat
        Env().log(f"auto-upgrade: aborted ({exc})")
    return 0


def _deliver_alert(env: Env, summary: str) -> bool:
    """The layer's single alert transport. Every trigger goes through here, so
    the golden rule stays intact: one megaphone, whatever wakes it up."""
    hostn = platform.node()
    msg = f"[AGENT ALERT] [{hostn}] {time.strftime('%Y-%m-%d %H:%M')}\n{summary}"
    msg = _localize_alert(env, msg)
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    webhook = os.environ.get("VAULT_ALERT_WEBHOOK")
    sent = False
    if token and chat:
        sent = _post_form(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": chat, "text": msg})
    elif webhook:
        sent = _post_form(webhook, {"host": hostn, "text": msg})
    if not sent and not IS_WINDOWS and resolve_cmd("notify-send"):
        r = _run_external(["notify-send", "-u", "critical", "-a", "agent-healthcheck",
                           "Agents: something is wrong", msg], timeout=5, capture_output=True)
        sent = r.returncode == 0
    return sent


STALE_GUARD_SECONDS_DEFAULT = 6 * 3600


def _last_guard_success(env: Env) -> int:
    """Epoch of the last completed healthcheck, i.e. of the last guard run that
    got all the way to the end. 0 when it has never run on this machine."""
    state_file = env.log_dir / "agent-healthcheck.state"
    if not state_file.is_file():
        return 0
    head = state_file.read_text(encoding="utf-8").splitlines()
    return int(head[0]) if head and head[0].isdigit() else 0


def _notify_failure_cli(argv: list[str]) -> int:
    """Trigger for `OnFailure=`: a guardian unit went into a failed state.

    Deliberately not a second notifier: it composes a line and hands it to the
    one transport. Systemd passes the failed unit's name, which is the whole
    value of this path over waiting for the next guard to notice."""
    unit = argv[0] if argv else "a guardian unit"
    env = Env()
    summary = (f"FAIL {unit} could not run. The layer stops syncing until it does. "
               f"Check it with: systemctl --user status {unit}")
    if not _deliver_alert(env, summary):
        env.log(f"notify-failure: {summary} (no transport configured)")
    return 0


def _notify_stale_cli(argv: list[str]) -> int:
    """Trigger for the independent heartbeat: the guard has not completed in far
    too long.

    This exists because `OnFailure=` is not enough on its own. A job cancelled
    because a dependency failed does not put its unit into a failed state, so
    nothing fires -- which is exactly how a guard stayed dead for six days while
    every single run logged `Dependency failed` and told no one. Elapsed time
    since the last completed run catches that, and every other cause too,
    without needing to know what broke."""
    del argv
    env = Env()
    try:
        limit = int(os.environ.get("AGENT_STALE_GUARD_SECONDS") or STALE_GUARD_SECONDS_DEFAULT)
    except ValueError:
        limit = STALE_GUARD_SECONDS_DEFAULT
    last = _last_guard_success(env)
    if last and (int(time.time()) - last) < limit:
        return 0
    hours = "never" if not last else f"{(int(time.time()) - last) // 3600}h ago"
    summary = (f"FAIL the agent sync has not completed since {hours}. Machines and skills "
               "are drifting apart in silence. Run: agent-sync guard")
    if not _deliver_alert(env, summary):
        env.log(f"notify-stale: {summary} (no transport configured)")
    return 0


def creds_health(env: Env, *, do_creds: bool, do_health: bool) -> None:
    if do_creds:
        try:
            _ensure_alert_creds(env)
        except Exception as exc:
            env.log(f"alert-creds: provisioning failed ({exc})")
    if do_health:
        try:
            _load_env_conf(env)
        except Exception as exc:
            # Same resilience as _ensure_alert_creds/_send_healthcheck right
            # above/below: a malformed or non-UTF-8 conf file (a stray binary
            # write, a bad manual edit) must not skip _send_healthcheck
            # entirely by letting the exception propagate past it unguarded.
            env.log(f"alert-creds: 91-telegram-alert.conf unreadable, skipping ({exc})")
        try:
            _send_healthcheck(env)
        except Exception as exc:
            env.log(f"healthcheck: non-fatal error ({exc})")


def _parse_cli(argv: list[str]) -> tuple[str, bool, bool, bool, list[str]]:
    skip_mcp = False
    allow_offline = False
    require_ready = False
    cleaned: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-Mode", "--mode"):
            if i + 1 >= len(argv):
                return arg, skip_mcp, allow_offline, require_ready, []
            cleaned.append(argv[i + 1])
            i += 2
            continue
        if arg in ("-InstallScheduledTask", "--install-scheduled-task"):
            # Backward-compatible no-op: B2.5 installs/repairs the scheduler
            # during every apply/guard run.
            i += 1
            continue
        if arg in ("-SkipMcp", "--skip-mcp"):
            skip_mcp = True
            i += 1
            continue
        if arg == "--allow-offline":
            allow_offline = True
            i += 1
            continue
        if arg == "--require-ready":
            require_ready = True
            i += 1
            continue
        cleaned.append(arg)
        i += 1
    return (cleaned[0] if cleaned else "help"), skip_mcp, allow_offline, require_ready, cleaned[1:]


# ── vault-push (cross-platform port of vault-push.sh's exact behavior) ─────
# vault-push.sh/.ps1 are now thin OS wrappers that exec/forward into this
# subcommand (see docs/sync-contract.md and vault-write-architecture.md):
# one Python implementation instead of maintaining the git-commit/rebase/
# mirror logic twice. tests/test_vault_push.py (the POSIX acceptance
# harness) exercises this through the bash wrapper and must keep passing
# unchanged; tests/test_vault_push_python.py exercises this entry point
# directly, cross-platform.

class _VaultPushUsageError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _parse_vault_push_args(argv: list[str]) -> tuple[str, list[str]]:
    """Mirrors vault-push.sh's own `while [ $# -gt 0 ]` loop: -m MSG, glued
    -mMSG, `--` stops flag parsing and takes every remaining argument as a
    file verbatim (even one that looks like -m), anything else is a file."""
    msg: str | None = None
    files: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "-m":
            if i + 1 >= len(argv):
                raise _VaultPushUsageError("argument missing for -m")
            msg = argv[i + 1]
            i += 2
            continue
        if arg.startswith("-m") and arg != "-m":
            msg = arg[2:]
            i += 1
            continue
        if arg == "--":
            files.extend(argv[i + 1:])
            break
        files.append(arg)
        i += 1
    if not msg:
        raise _VaultPushUsageError('needs -m "message"')
    return msg, files


def _vault_push_publish(env: Env) -> int:
    if _git(env, "push", env.remote, env.branch).returncode == 0:
        print(f"vault-push: push {env.remote} OK")
    else:
        if _git(env, "fetch", "--prune", env.remote, env.branch).returncode != 0:
            print(f"vault-push: {env.remote} OFFLINE — the commit stays local; run agent-sync publish later")
            return 1
        status = _git(env, "status", "--porcelain", "--untracked-files=no")
        if status.stdout.strip():
            print(f"vault-push: {env.remote} rejected but the working tree has uncommitted changes — NOT rebasing, resolve by hand")
            return 1
        if _git(env, "rebase", f"{env.remote}/{env.branch}").returncode == 0:
            if _git(env, "push", env.remote, env.branch).returncode != 0:
                print(f"vault-push: {env.remote} still rejected after rebase — try again")
                return 1
            print(f"vault-push: push {env.remote} OK (after a clean rebase)")
        else:
            _git(env, "rebase", "--abort")
            print(f"vault-push: {env.remote} DIVERGENCE WITH CONFLICT — needs a manual 'git pull --rebase {env.remote} {env.branch}'")
            return 1

    # Mirrors are explicit downstream replicas: never rewrite the canonical
    # local history, never affect the exit code. A stale mirror is aligned
    # with force-with-lease only after the authoritative remote already
    # accepted the same commit.
    for mirror in env.mirrors:
        if _git(env, "remote", "get-url", mirror).returncode != 0:
            print(f"vault-push: mirror '{mirror}' is not configured; skipped")
            continue
        if _git(env, "push", mirror, env.branch).returncode == 0:
            print(f"vault-push: push mirror {mirror} OK")
        elif (_git(env, "fetch", "--prune", mirror, env.branch).returncode == 0
              and _git(env, "push", "--force-with-lease", mirror, env.branch).returncode == 0):
            print(f"vault-push: mirror {mirror} aligned to authoritative {env.remote}")
        else:
            print(f"vault-push: mirror {mirror} not updated; authoritative {env.remote} is safe")
    return 0


def _vault_push_locked(env: Env, msg: str, files: list[str]) -> int:
    # Local-Only sentinel (same "local"/"none" values publish() already
    # special-cases): no remote is ever meant to exist, so skip the "is it
    # configured" check below instead of failing on a git remote that was
    # never supposed to be there. The commit itself still happens further
    # down -- Local-Only means no publication target, not no local history.
    local_only = env.remote in ("local", "none")
    if not local_only and _git(env, "remote", "get-url", env.remote).returncode != 0:
        print(f"vault-push: authoritative remote '{env.remote}' is not configured")
        return 1

    if files:
        if _git(env, "add", "--", *files).returncode != 0:
            print("vault-push: git add failed")
            return 1
        # Carry the same pathspec through the emptiness probe and the commit.
        # `add` was already scoped, but the probe and the commit were not, so
        # anything the caller happened to have in the index rode along into a
        # commit whose message named one file. That is load-bearing for the
        # engine-pin write, which nexgen_update.py documents as containing
        # only that exact file. A partial commit leaves the caller's other
        # staged entries staged; it does not discard them.
        probe = _git(env, "diff", "--cached", "--quiet", "--", *files)
        commit_args = ["commit", "-q", "-m", msg, "--", *files]
    else:
        probe = _git(env, "diff", "--cached", "--quiet")
        commit_args = ["commit", "-q", "-m", msg]
    if probe.returncode == 0:
        print("vault-push: nothing staged, nothing to commit")
        return 0

    if _git(env, *commit_args).returncode != 0:
        print("vault-push: commit failed")
        return 1
    short = _git(env, "rev-parse", "--short", "HEAD").stdout.strip()
    print(f"vault-push: commit {short}")

    if local_only:
        print(f"vault-push: push skipped (Local-Only mode, remote={env.remote})")
        return 0

    return _vault_push_publish(env)


def _vault_push_cli(argv: list[str]) -> int:
    try:
        msg, files = _parse_vault_push_args(argv)
    except _VaultPushUsageError as exc:
        print(f"vault-push: {exc.message}", file=sys.stderr)
        return 2

    try:
        env = Env()
    except RemoteConfigError as exc:
        print(f"vault-push: {exc}", file=sys.stderr)
        return 2

    if not env.vault_data.is_dir():
        print(f"vault-push: vault not found ({env.vault_data})")
        return 1

    # Same host-wide lock file agent_sync.py's own apply/guard/publish runs
    # use by default (env.log_dir / "agent-sync.lock"), acquired the same
    # way (SyncRunLock, fcntl.flock/msvcrt.locking): without this, a
    # `vault-push` running concurrently with an apply/guard cycle could
    # interleave a commit with a mid-apply working tree.
    lock_file = Path(os.environ.get("AGENT_SYNC_LOCK_FILE") or str(env.log_dir / "agent-sync.lock"))
    try:
        lock_timeout = float(os.environ.get("AGENT_SYNC_LOCK_TIMEOUT_SECONDS") or LOCK_TIMEOUT_DEFAULT)
    except ValueError:
        print("vault-push: AGENT_SYNC_LOCK_TIMEOUT_SECONDS must be numeric", file=sys.stderr)
        return 2

    with SyncRunLock(lock_file, timeout=lock_timeout) as lock:
        if not lock.acquired:
            print("vault-push: sync lock busy (another agent-sync/vault-push is running) -- aborting", file=sys.stderr)
            return 75
        return _vault_push_locked(env, msg, files)


def _print_config(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"authoritative_remote", "mirrors"}:
        print("Use: agent-sync config authoritative_remote|mirrors", file=sys.stderr)
        return 2
    try:
        config = load_remote_config()
    except RemoteConfigError as exc:
        print(f"agent_sync: {exc}", file=sys.stderr)
        return 2
    if argv[1] == "authoritative_remote":
        print(config.authoritative_remote)
    else:
        print("\n".join(config.mirrors))
    return 0


def _run_phase(env: Env, name: str, fn: Callable[[Env], object]) -> bool:
    try:
        result = fn(env)
    except Exception as exc:
        env.log(f"phase {name}: ERROR ({type(exc).__name__}: {exc})")
        return False
    if result is False:
        env.log(f"phase {name}: ERROR (reported incomplete)")
        return False
    env.log(f"phase {name}: ok")
    return True


# ── entry point ──────────────────────────────────────────────────────────

def _skill_manifest_names(path: Path) -> "set[str] | None":
    """Canonical skill names = the keys of the manifest's `skills:` mapping
    (same shape as the MCP manifest's `servers:`). None when the manifest is
    absent or unreadable, so the caller skips the skill section rather than
    reporting a false 'everything is stray'."""
    if not path.is_file():
        return None
    import yaml
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, dict):
        return None
    return set(skills.keys())


def _file_digest(path: Path) -> str | None:
    """SHA-256 of a file, or None when it cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _bootstrap_alignment(kind: str, path: Path, canon: Path, canon_digest: str | None) -> str:
    """Describe whether a CLI's bootstrap actually carries the canonical
    content, not merely that some file exists at that path.

    Reporting `present` alone was misleading (found 2026-07-26): on a host
    without symlink privilege the per-CLI bootstrap is a real copy, and a copy
    from three weeks ago is exactly as `present` as one written a second ago.
    The CLI reading it looks confused rather than out of date, which is a hard
    failure to diagnose from the outside.

    The three kinds are deliberately different, because the mechanism is:
      pointer -- Claude reads a short file that REFERENCES the canonical one,
                 so identical content would be the bug, not the goal.
      mirror  -- Codex/Antigravity read the file itself: a symlink where the
                 host allows one, a real copy otherwise.
      config  -- OpenCode carries the canonical path as an entry in its own
                 config, verified by the doctor rather than by content.
    """
    if not path.exists():
        return "not configured on this machine"
    if kind == "pointer":
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "present but unreadable"
        if str(canon) in text:
            return "pointer -> canonical bootstrap"
        return "present but does NOT reference the canonical bootstrap -- run: agent-sync apply"
    if kind == "config":
        return "present (the canonical path is an entry in this CLI's own config)"
    try:
        if path.is_symlink() and path.resolve() == canon.resolve():
            return "link -> canonical bootstrap"
    except OSError:
        pass
    if canon_digest is None:
        return "present, but the canonical bootstrap is unreadable so alignment is unknown"
    if _file_digest(path) == canon_digest:
        return "real copy, identical right now (re-aligns only when agent-sync runs)"
    return "real copy, DIVERGED from the canonical bootstrap -- run: agent-sync apply"


def _skill_inventory(manifest_names, materialized):
    """Split materialized skills into canonical (in the manifest), out-of-manifest
    extras, and manifest entries not yet materialized. Pure and testable."""
    canonical = sorted(n for n in materialized if n in manifest_names)
    extras = sorted(n for n in materialized if n not in manifest_names)
    missing = sorted(n for n in manifest_names if n not in materialized)
    return canonical, extras, missing


def _claude_memory_stats(projects_dir: Path):
    """Count Claude Code native-memory fact files per project
    (~/.claude/projects/<enc>/memory/*.md). These are structured, durable facts
    eligible for confluence into the vault (unlike the other CLIs' session
    transcripts). Pure and testable; returns [(project, fact_count), ...]."""
    stats = []
    if not projects_dir.is_dir():
        return stats
    for proj in sorted(projects_dir.iterdir()):
        mem = proj / "memory"
        if not mem.is_dir():
            continue
        facts = [p for p in mem.iterdir() if p.is_file() and p.suffix == ".md"]
        stats.append((proj.name, len(facts)))
    return stats


def _inventory_cli(argv: list[str]) -> int:
    """Read-only onboarding scan of the whole setup: MCP servers (via
    render.py --inventory), skills (manifest vs materialized library), and
    per-CLI bootstrap presence. Foundation of the adopt/reset onboarding flow;
    never writes. Exit 0 (report) or 2 on an env/config error."""
    if argv:
        print("agent_sync: inventory takes no arguments", file=sys.stderr)
        return 2
    try:
        env = Env()
    except RemoteConfigError as exc:
        print(f"agent_sync: {exc}", file=sys.stderr)
        return 2

    mcp_rc = 0
    render_path = env.ul / "mcp" / "render.py"
    if render_path.is_file():
        r = _run_python_script([sys.executable, str(render_path), "--inventory"])
        mcp_rc = r.returncode
        sys.stdout.write(r.stdout)
        if r.stderr.strip():
            sys.stderr.write(r.stderr)
    else:
        print(f">>> renderer not found ({render_path}); MCP inventory skipped")

    print("")
    print(">>> Onboarding inventory -- skills (manifest vs materialized library):")
    manifest_names = _skill_manifest_names(env.instance_ul / "skills" / "skills.manifest.yaml")
    if manifest_names is None:
        print("  skills manifest absent or unreadable -- skill inventory skipped")
    else:
        if env.skill_library.is_dir():
            materialized = [p.name for p in env.skill_library.iterdir() if p.is_dir()]
        else:
            materialized = []
        canonical, extras, missing = _skill_inventory(manifest_names, materialized)
        print(f"  {len(materialized)} materialized skill(s) -- {len(canonical)} canonical, {len(extras)} out-of-manifest")
        if extras:
            print(f"      out-of-manifest: {', '.join(extras)}")
        if missing:
            print(f"      in manifest but not materialized: {', '.join(missing)}")

    print("")
    print(">>> Onboarding inventory -- bootstrap per CLI (read-only):")
    canon = env.instance_ul / "instructions" / "AGENTS.md"
    canon_digest = _file_digest(canon)
    bootstraps = [
        ("claude", env.home / "CLAUDE.md", "pointer"),
        ("codex", env.home / ".codex" / "AGENTS.md", "mirror"),
        ("antigravity", env.home / ".gemini" / "config" / "AGENTS.md", "mirror"),
        ("opencode", _opencode_config_path(env.home), "config"),
    ]
    for label, path, kind in bootstraps:
        print(f"  {label}: {_bootstrap_alignment(kind, path, canon, canon_digest)}")

    print("")
    print(">>> Onboarding inventory -- native memory (read-only):")
    mem_stats = _claude_memory_stats(env.home / ".claude" / "projects")
    if mem_stats:
        total = sum(n for _, n in mem_stats)
        with_facts = [p for p in mem_stats if p[1]]
        print(f"  claude: {total} structured memory fact(s) across {len(with_facts)} project(s) -- confluence-eligible into the vault")
    else:
        print("  claude: no structured native memory found")
    transcript_stores = [
        ("codex", env.home / ".codex" / "sessions"),
        ("opencode", env.home / ".local" / "share" / "opencode" / "storage"),
        ("antigravity", env.home / ".gemini" / "antigravity-cli"),
    ]
    for label, path in transcript_stores:
        if path.exists():
            print(f"  {label}: session transcripts present -- distillation deferred to a later release, not imported")

    print("")
    print(">>> Read-only. Adopt (canonize) or reset what's out-of-manifest via the onboarding flow.")
    return 2 if mcp_rc != 0 else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(HELP_TEXT)
        return 0
    if argv[0] == "config":
        return _print_config(argv)
    if argv[0] == "vault-push":
        return _vault_push_cli(argv[1:])
    if argv[0] == "inventory":
        return _inventory_cli(argv[1:])
    if argv[0] == "notify-failure":
        return _notify_failure_cli(argv[1:])
    if argv[0] == "notify-stale":
        return _notify_stale_cli(argv[1:])
    if argv[0] == "heartbeat":
        return _heartbeat_cli(argv[1:])

    mode, skip_mcp, allow_offline, require_ready, extras = _parse_cli(argv)
    if mode not in MODES:
        print(f"agent_sync: unknown mode: {mode}\nUse: agent_sync --help", file=sys.stderr)
        return 2
    if extras:
        print(f"agent_sync: unexpected arguments: {' '.join(extras)}", file=sys.stderr)
        return 2
    if allow_offline and mode != "apply":
        print("agent_sync: --allow-offline is accepted only with manual apply", file=sys.stderr)
        return 2
    if require_ready and mode != "apply":
        print("agent_sync: --require-ready is accepted only with manual apply", file=sys.stderr)
        return 2
    try:
        env = Env()
    except RemoteConfigError as exc:
        print(f"agent_sync: {exc}", file=sys.stderr)
        return 2

    flags = MODES[mode]
    try:
        lock_timeout = float(os.environ.get("AGENT_SYNC_LOCK_TIMEOUT_SECONDS") or LOCK_TIMEOUT_DEFAULT)
    except ValueError:
        print("agent_sync: AGENT_SYNC_LOCK_TIMEOUT_SECONDS must be numeric", file=sys.stderr)
        return 2

    with SyncRunLock(env.lock_path, timeout=lock_timeout) as lock:
        if not lock.acquired:
            env.log(f"agent-sync: lock busy, skipped mode={mode}")
            if mode == "guard":
                # Exit 0 on purpose (a colliding manual apply is a normal,
                # legitimate contender for the host-wide lock), but the skip
                # must still be observable: without this line a guard cycle
                # that did nothing looks identical to a successful one in
                # journalctl, so a sync that never completes -- e.g. a slow
                # apply meeting every timer run -- stays invisible forever.
                print("agent_sync: guard skipped: another sync run holds the lock", file=sys.stderr)
                return 0
            print("agent_sync: another sync run is active", file=sys.stderr)
            return 75

        env.log(
            f"agent-sync: start mode={mode} authoritative_remote={env.remote} "
            f"config_source={env.remote_config_source}"
        )
        errors: list[str] = []
        apply_allowed = True

        if flags["pull"]:
            outcome = pull(env)
            if outcome.allows_apply:
                pass
            elif outcome.state is PullState.FETCH_FAILED and allow_offline:
                env.log(f"pull: manual offline override accepted ({outcome.message})")
            else:
                errors.append(f"pull:{outcome.state.value}")
                apply_allowed = False

        needs_preflight = flags["apply"] or mode == "preflight"
        if needs_preflight and apply_allowed:
            if not _run_phase(env, "preflight", preflight):
                errors.append("preflight")
                apply_allowed = False

        if flags["apply"] and apply_allowed:
            phases: list[tuple[str, Callable[[Env], object]]] = [
                ("data_migrations", data_migrations),
            ]
            if skip_mcp:
                env.log("mcp-gen: skipped by explicit --skip-mcp")
            else:
                phases.append(("mcp_render", mcp_render))
            phases.extend([
                # instructions AFTER mcp_render, deliberately. mcp_render now
                # bootstraps the config file of a CLI that is installed but was
                # never launched, and OpenCode's only bootstrap pointer is an
                # entry INSIDE that same file. With instructions first, the
                # opening apply found no file, skipped the pointer, and only a
                # second run converged: self-healing on MULTI, where the timer
                # re-runs, and permanent on MINIMAL, which has no timer at all.
                ("instructions", instructions),
                ("utils", utils),
                ("local_model_runtime", local_model_runtime),
                ("install_scheduler", install_scheduler),
                # Render Antigravity's canonical source before propagating it.
                # On Windows without symlink privilege make_link() falls back
                # to a real copy, so the old order could copy stale JSON and
                # leave derivatives one generation behind.
                ("antigravity_mcp", antigravity_mcp),
                ("vault_skills", vault_skills),
                ("runtimes", runtimes),
                ("skills_index", skills_index),
                ("claude_hooks", claude_hooks),
                # After claude_hooks: both write settings.json, and running the
                # engine-owned checkpoint hook first keeps the instance policy
                # as the last word on posture.
                ("claude_permissions", claude_permissions),
            ])
            for name, fn in phases:
                if not _run_phase(env, name, fn):
                    errors.append(name)
        elif flags["apply"]:
            env.log("apply: BLOCKED because the authoritative data state is not safe")

        if flags["push"] and not _run_phase(env, "publish", publish):
            errors.append("publish")

        creds_health(env, do_creds=flags["creds"], do_health=flags["health"])

        dirty = _git(env, "status", "--porcelain")
        dirty_lines = [line for line in dirty.stdout.splitlines() if line.strip()]
        if dirty_lines:
            env.log(f"note: {len(dirty_lines)} uncommitted file(s) in the vault (not touching them)")

        if errors:
            env.log(f"agent-sync: completed mode={mode} status=failed errors={','.join(errors)}")
            # Also stderr, not just the log file: the recurring guard run is
            # normally launched by systemd (or Task Scheduler on Windows),
            # neither of which prints agent-sync.log's content anywhere --
            # journalctl only shows "Failed with result 'exit-code'" with no
            # clue which phase or why. Stay silent on stdout on the success
            # path below (no spam when everything is fine); only a failure
            # earns a line here, and it names the phase(s), the error(s), and
            # where the full detail lives.
            print(
                f"agent_sync: FAILED mode={mode} phase(s)={','.join(errors)} "
                f"-- see {env.log_path} for detail",
                file=sys.stderr,
            )
            return 1
        if mode == "apply":
            try:
                readiness_timeout = int(os.environ.get("AGENT_READY_TIMEOUT_SECONDS") or "90")
            except ValueError:
                readiness_timeout = 90
            readiness = _doctor_summary(env, timeout=readiness_timeout, strict=True)
            match = re.search(r"FAIL=(\d+)", readiness or "")
            ready = bool(match and int(match.group(1)) == 0)
            state = "READY" if ready else "PARTIAL"
            detail = readiness or "strict doctor did not return a readable summary"
            print(f"agent-sync: BASE installed; readiness={state}. {detail}")
            env.log(f"agent-sync: completed mode=apply status={state.lower()} summary={detail}")
            if require_ready and not ready:
                return 1
            return 0
        env.log(f"agent-sync: completed mode={mode} status=ok")
        return 0


if __name__ == "__main__":
    sys.exit(main())

"""The guard cycle (Guard) and transactional sync for NeXgen Engine v2.

Phases of the guard cycle (guard / apply):
1. Host-wide lock (guard busy = exit 0, apply busy = exit 75)
2. Git inspection (checks clean state, fetches from the authoritative remote)
3. Preflight (read-only validation of configs and schemas)
4. Skill materialization (skills.py)
5. MCP configuration generation (renderer.py)
6. Instruction-pointer alignment (AGENTS.md -> CLAUDE.md / .gemini / .codex)
7. Alignment check execution
8. Liveness registration (agent-guard-liveness)
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from nexgen_core.action_notes import ERROR, WARN, is_error, is_warning  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.beat import Heartbeat
from nexgen_core.errors import AlignmentError
from nexgen_core.config import load_mcp_manifest, load_skills_manifest
from nexgen_core.files import atomic_write_text
from nexgen_core.git_ops import (
    GitState,
    auto_commit_infra_files,
    fast_forward_merge,
    get_current_branch,
    get_untracked_infra_files,
    inspect_git_state,
    quarantine_diverged_commits,
    resolve_remotes,
    run_git,
)
from nexgen_core.i18n import t
from nexgen_core.jsonc import parse_jsonc, set_jsonc_top_level_value
from nexgen_core.lock import LockTimeoutError, host_mutation
from nexgen_core.paths import (
    resolve_engine_root,
    resolve_home,
    resolve_state_dir,
    resolve_vault_data,
)
from nexgen_core.renderer import McpRenderer
from nexgen_core.runtimes import apply_all as apply_runtimes
from nexgen_core.scheduler import install_scheduler
from nexgen_core.skills import SkillMaterializer

logger = logging.getLogger(__name__)


def _same_file(a: str, b: Path) -> bool:
    """True if both name the same file, whatever the spelling (absolute or ~)."""
    try:
        return Path(a).expanduser().resolve() == b.expanduser().resolve()
    except OSError:
        return a == str(b)


class GuardMode(str, Enum):
    GUARD = "guard"
    APPLY = "apply"
    PULL = "pull"
    PREFLIGHT = "preflight"


@dataclass
class GuardResult:
    success: bool
    mode: GuardMode
    message: str
    exit_code: int = 0
    actions_taken: list[str] = field(default_factory=list)


class GuardRunner:
    """Executor for sync and guard transactions."""

    def __init__(
        self,
        vault_data: Path | None = None,
        engine_root: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self.home = resolve_home(home)
        _v = resolve_vault_data(self.home, vault_data)
        self.vault_data = _v
        self.engine_root = resolve_engine_root(self.home, engine_root)
        self.state_dir = resolve_state_dir(self.home)
        self.heartbeat = Heartbeat(
            vault_data=self.vault_data, engine_root=self.engine_root, home=self.home
        )

    def preflight(self) -> tuple[bool, str]:
        """Read-only validation of all configuration files.

        Strict: a typo that would silently drop a connector or skill fails
        here, before any phase writes half a world.
        """
        manifest_mcp = self.vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
        manifest_skills = self.vault_data / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"

        try:
            if manifest_mcp.is_file():
                load_mcp_manifest(manifest_mcp, strict=True)
            if manifest_skills.is_file():
                load_skills_manifest(manifest_skills, strict=True)
            return True, t("MCP and Skill configurations valid")
        except Exception as exc:  # noqa: BLE001 - phase failure is recorded, never raises
            return False, t("Preflight failed: {error}", error=exc)

    def align_instructions(self) -> list[str]:
        """Aligns the instruction compatibility pointers (~/CLAUDE.md etc.)."""
        canon = self.vault_data / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
        if not canon.is_file():
            return []

        actions: list[str] = []
        claude_md = self.home / "CLAUDE.md"
        content = (
            "# Claude compatibility pointer\n\n"
            "Canonical instructions live at:\n"
            f"{canon}\n\n"
            "At session start, read and follow that file when the user-specific agent policy is needed.\n"
            "Do not duplicate the full bootstrap in CLAUDE.md.\n"
        )

        if not claude_md.is_file() or claude_md.read_text(encoding="utf-8") != content:
            # The file may contain hand-written lines. Regenerating it is
            # fine; making it disappear without a copy is not: if the
            # safety copy itself fails, stop instead of destroying the
            # only copy of those lines.
            if claude_md.is_file():
                from nexgen_core.files import backup_file

                try:
                    backup_file(claude_md, tag="instructions")
                except OSError as exc:
                    actions.append(WARN + t(
                        "instruction pointer {path} left untouched: safety backup failed ({error})",
                        path=claude_md, error=exc,
                    ))
                else:
                    atomic_write_text(claude_md, content)
                    actions.append(t("Updated instruction pointer {path}", path=claude_md))
            else:
                atomic_write_text(claude_md, content)
                actions.append(t("Updated instruction pointer {path}", path=claude_md))

        # The other three CLIs read the canonical file directly. Aligning
        # only one of them would mean having a canonical source for one
        # runtime and three stale copies for the others, which is the
        # opposite of the invariant.
        for label, target in (
            ("codex", self.home / ".codex" / "AGENTS.md"),
            ("antigravity", self.home / ".gemini" / "config" / "AGENTS.md"),
        ):
            changed, warn = self._link_to_canonical(target, canon)
            if changed:
                actions.append(t("{label} instructions restored to canonical", label=label))
            if warn:
                actions.append(warn)

        opencode_action = self._align_opencode_instructions(canon)
        if opencode_action:
            actions.append(opencode_action)
        dead_array_action = self._drop_dead_opencode_instructions_array()
        if dead_array_action:
            actions.append(dead_array_action)

        return actions

    def _link_to_canonical(self, target: Path, canon: Path) -> tuple[bool, str | None]:
        """Points `target` at the canonical file.

        Returns (changed, warning): a failed safety copy skips the target
        with a warning instead of silently leaving a stale pointer behind.
        """
        try:
            if target.is_symlink() and target.resolve() == canon.resolve():
                return False, None
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                # A real copy may contain hand-written lines. If the safety
                # copy itself fails, stop here: unlinking anyway would destroy
                # the only copy of those lines.
                if target.is_file() and not target.is_symlink():
                    from nexgen_core.files import backup_file

                    try:
                        backup_file(target, tag="instructions")
                    except OSError as exc:
                        return False, (WARN + t(
                            "instruction pointer {path} left untouched: safety backup failed ({error})",
                            path=target, error=exc,
                        ))
            try:
                from nexgen_core.files import publish_symlink

                publish_symlink(target, canon)
            except OSError:
                # Windows without symlink privileges: a copy beats nothing.
                # publish_symlink never leaves the target missing, so a copy
                # fallback only runs when the atomic path failed.
                try:
                    if target.is_symlink() or target.is_file():
                        target.unlink()
                    else:
                        shutil.copy2(canon, target)
                        return True, None
                except OSError:
                    pass
                shutil.copy2(canon, target)
            return True, None
        except OSError as exc:
            return False, (WARN + t(
                "instruction pointer {path} not aligned ({error})", path=target, error=exc,
            ))

    def _align_opencode_instructions(self, canon: Path) -> str | None:
        """Points OpenCode V2 at the canonical file through the scope file it
        actually loads (`~/.config/opencode/AGENTS.md`).

        The V2 config schema accepts an `instructions` array but does not
        resolve its files (official docs, verified 2026-09-22): a green
        doctor on that array was a false green. The scope file is a symlink
        at the canonical bootstrap, exactly like Codex and Antigravity --
        no content is ever copied, so no private policy or identity can leak
        into the public engine through this path.

        A REAL file (not a symlink) is left untouched: on a machine with the
        private identity layer it is that layer's derivative, and replacing
        it with a pointer would destroy the persona. The doctor reports that
        case separately instead of "fixing" it here.
        """
        from nexgen_core.paths import opencode_agents_file

        target = opencode_agents_file(self.home)
        if target.is_symlink() or not target.exists():
            changed, warn = self._link_to_canonical(target, canon)
            if changed:
                return t("opencode instructions restored to canonical")
            return warn
        # A real file: private derivative or hand-written. Not ours to take.
        return None

    def _drop_dead_opencode_instructions_array(self) -> str | None:
        """Removes engine-added canonical entries from the dead V1 array.

        The array is accepted but unresolved by V2, so entries the old guard
        added only pretend to load the bootstrap. Entries equal to the
        canonical path go away (backup first); anything else -- a choice the
        user made -- stays exactly as it is.
        """
        from nexgen_core.paths import canonical_instructions

        canon = canonical_instructions(self.vault_data)
        renderer = McpRenderer(vault_data=self.vault_data, home=self.home)
        candidate = renderer.opencode_config_path()
        if not candidate.is_file():
            return None
        try:
            raw = candidate.read_text(encoding="utf-8")
            data = parse_jsonc(raw) if candidate.suffix == ".jsonc" else json.loads(raw or "{}")
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("instructions"), list):
            return None
        entries = data["instructions"]
        kept = [e for e in entries if not (isinstance(e, str) and _same_file(e, canon))]
        if len(kept) == len(entries):
            return None
        try:
            if candidate.suffix == ".jsonc" and raw.strip():
                if kept:
                    body = set_jsonc_top_level_value(raw, "instructions", kept)
                else:
                    from nexgen_core.jsonc import remove_jsonc_top_level_value

                    body = remove_jsonc_top_level_value(raw, "instructions")
            else:
                data["instructions"] = kept
                if not kept:
                    del data["instructions"]
                body = json.dumps(data, indent=2) + "\n"
            from nexgen_core.files import backup_file

            with contextlib.suppress(OSError):
                backup_file(candidate, tag="instructions")
            atomic_write_text(candidate, body)
        except OSError:
            return None
        return t("opencode dead 'instructions' entries removed")

    def align_local_model_runtime(self) -> list[str]:
        """Windows-only: relinks the private local-model-agent.ps1 adapter (bring-your-own).

        Port of the release's local_model_runtime step: the vault can supply
        a private adapter (never in the public product); if it's absent
        that's the expected default, not an error. Also installs the stable
        local-worker/local-agent wrappers.
        """
        if sys.platform != "win32":
            return []
        src = self.vault_data / "03-INFRA" / "scripts" / "local-model-agent.ps1"
        if not src.is_file():
            return []
        local_bin = self.home / ".local" / "bin"
        local_bin.mkdir(parents=True, exist_ok=True)
        actions: list[str] = []
        runtime = local_bin / "local-model-agent.ps1"
        try:
            if runtime.is_symlink() or runtime.exists():
                runtime.unlink(missing_ok=True)
        except OSError as exc:
            # An optional adapter must never fail the whole apply: on
            # Windows a locked .ps1 is routine, not corruption.
            actions.append(WARN + t("local-model: adapter not relinked ({error})", error=exc))
            return actions
        try:
            runtime.symlink_to(src)
            actions.append(t("local-model: relinked local-model-agent.ps1"))
        except OSError:
            shutil.copy2(src, runtime)
            actions.append(t("local-model: copied local-model-agent.ps1"))
        wrappers = {
            "local-worker.ps1": "$ScriptPath = Join-Path $PSScriptRoot 'local-model-agent.ps1'\r\n& $ScriptPath -Mode worker @args\r\n",
            "local-agent.ps1": "$ScriptPath = Join-Path $PSScriptRoot 'local-model-agent.ps1'\r\n& $ScriptPath -Mode agent @args\r\n",
        }
        for name, content in wrappers.items():
            target = local_bin / name
            if not target.is_file() or target.read_text(encoding="utf-8", errors="replace") != content:
                atomic_write_text(target, content)
                actions.append(t("local-model: installed wrapper {name}", name=name))
        return actions

    def _refresh_update_cache_best_effort(self) -> None:
        """Records the newest released tag for the shell-startup notice.

        The guard already talked to the network (git inspection fetches),
        so one read-only `ls-remote` more is marginal -- and it is what
        feeds `nexgen tool update-notifier --shell-check` without any
        network at shell startup. Silent by design: an offline machine
        keeps yesterday's cache, which beats an error in a recurring job.
        """
        try:
            from nexgen_core.tools.update_notifier import refresh_update_cache

            probe = run_git(self.engine_root, "rev-parse", "--show-toplevel")
            if probe.returncode == 0 and probe.stdout.strip():
                refresh_update_cache(probe.stdout.strip())
        except Exception as exc:  # noqa: BLE001 - offline cache refresh never fails the guard
            logger.debug("background update-cache refresh skipped (%s)", type(exc).__name__)

    def apply_runtime_permissions(self) -> list[str]:
        """Permission posture + guardrail hook for every installed CLI.

        The POLICY -- which posture, which guardrail body -- is Vault private
        data (03-INFRA/agent-universal-layer/permissions/manifest.yaml),
        never the public engine's: without that file this phase is a
        complete no-op, so no end user inherits someone else's permission
        posture. The mechanism that applies it lives in nexgen_core.runtimes;
        here we only read the manifest and translate it into plain arguments
        for that mechanism.
        """
        manifest_path = self.vault_data / "03-INFRA" / "agent-universal-layer" / "permissions" / "manifest.yaml"
        engine_hooks_dir = self.engine_root / "agent-universal-layer" / "hooks"
        event_sink_source = engine_hooks_dir / "nexgen-event-sink.mjs"
        sink_wanted = self._event_sink_wanted()
        sink_args = {
            "event_sink_source": event_sink_source if sink_wanted and event_sink_source.is_file() else None,
            "remove_event_sink": sink_wanted is False,
        }
        if not manifest_path.is_file():
            # No permission policy here, but the event sink does not depend on one.
            if sink_args["event_sink_source"] is None and not sink_args["remove_event_sink"]:
                return []
            return apply_runtimes(home=self.home, engine_hooks_dir=engine_hooks_dir, **sink_args)
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            return [WARN + t("runtime-permissions: could not read {path} ({error})", path=manifest_path, error=exc)]
        if not isinstance(raw, dict):
            return [WARN + t("runtime-permissions: the root of {path} is not a map", path=manifest_path)]

        posture = {
            cli: value
            for cli, value in (raw.get("posture") or {}).items()
            if isinstance(cli, str) and isinstance(value, str)
        }

        # Only one guardrail policy is supported (the first declared hook):
        # it's the only real case, and generalizing to an arbitrary list of
        # events/matchers per CLI is exactly the five-map complexity this
        # package replaces.
        guardrail_source: Path | None = None
        for spec in raw.get("hooks") or []:
            if not isinstance(spec, dict) or not isinstance(spec.get("file"), str):
                continue
            candidate = (manifest_path.parent / spec["file"]).resolve()
            try:
                parent_resolved = manifest_path.parent.resolve()
                is_inside = candidate.is_relative_to(parent_resolved)
            except (OSError, ValueError):
                is_inside = False
            if not is_inside:
                name = spec.get("name", spec["file"])
                return [WARN + t("runtime-permissions: {name} escapes permissions/, guardrail rejected", name=name)]
            hook_name = Path(spec["file"]).name
            if hook_name in (".", "..") or not re.fullmatch(r"[A-Za-z0-9._-]+", hook_name):
                name = spec.get("name", spec["file"])
                return [WARN + t("runtime-permissions: {name} has an unsafe hook filename, guardrail rejected", name=name)]
            if not candidate.is_file():
                return [WARN + t("runtime-permissions: guardrail body missing ({path})", path=candidate)]
            guardrail_source = candidate
            break

        return apply_runtimes(
            home=self.home,
            engine_hooks_dir=engine_hooks_dir,
            posture=posture,
            guardrail_source=guardrail_source,
            **sink_args,
        )

    def _event_sink_wanted(self) -> bool | None:
        """Does a module this machine declared need the event-sink hook?

        True/False when the module state says so; None when it cannot be read, in which case
        the hook is left as it is: removing it because of a read error would turn a transient
        fault into a lost integration. A module declared `local` but held back by missing env
        gates (a timer without the user's tokens) still counts: that is the environment, not a
        decision to remove it.
        """
        try:
            from nexgen_core.modules import modules_state

            states = modules_state(vault_data=self.vault_data, engine_root=self.engine_root)
        except Exception as exc:  # noqa: BLE001 - an unreadable module state changes nothing
            logger.debug("event sink left as it is: module state unreadable (%s)", type(exc).__name__)
            return None
        return any(
            "event_sink" in state.module.provides.runtime_hooks
            and "local" in (state.state, state.declared)
            for state in states
        )

    def run(
        self,
        mode: GuardMode = GuardMode.APPLY,
        allow_offline: bool = False,
        skip_mcp: bool = False,
    ) -> GuardResult:
        """Runs the requested cycle with locking and transactional safety.

        The orchestration only: lock, route by mode, run each phase in
        order, convert lock contention and unexpected errors into results.
        Every phase below is a `_phase_*` method so it can be read, tested
        and reordered on its own; a phase that must stop the cycle returns
        its `GuardResult`, otherwise `None`.
        """
        is_guard = (mode == GuardMode.GUARD)
        actions: list[str] = []

        try:
            with host_mutation(
                f"agent-sync-{mode.value}", state_dir=self.state_dir, is_guard=is_guard,
            ):
                auth_remote, _ = resolve_remotes(self.vault_data)
                branch = get_current_branch(self.vault_data) or "main"

                abort = self._phase_git(mode, allow_offline, auth_remote, branch, actions)
                if abort is not None:
                    return abort

                # Pull downloads without regenerating derived files, by
                # contract (`nexgen pull` help). The pulled content is still
                # validated read-only, and the message says apply is next:
                # otherwise the user believes they are synchronized while
                # every CLI still runs yesterday's configs.
                if mode == GuardMode.PULL:
                    self._refresh_update_cache_best_effort()
                    abort = self._phase_preflight(mode)
                    if abort is not None:
                        return abort
                    return GuardResult(
                        success=True, mode=mode,
                        message=t("Pull completed (derived files not regenerated: run apply next)"),
                        exit_code=0, actions_taken=actions,
                    )

                abort = self._phase_preflight(mode)
                if abort is not None:
                    return abort

                failed = self._run_phases(actions, branch, skip_mcp)
                self._phase_liveness(actions, is_guard, mode, failed)

                if failed:
                    return GuardResult(
                        success=False,
                        mode=mode,
                        message=t(
                            "Alignment ran every phase, but these failed: {phases} (see actions above)",
                            phases=", ".join(failed),
                        ),
                        exit_code=1,
                        actions_taken=actions,
                    )
                if any(is_warning(action) for action in actions):
                    return GuardResult(
                        success=True,
                        mode=mode,
                        message=t("Alignment completed with warnings (see actions above)"),
                        exit_code=0,
                        actions_taken=actions,
                    )
                return GuardResult(
                    success=True,
                    mode=mode,
                    message=t("Alignment completed successfully"),
                    exit_code=0,
                    actions_taken=actions,
                )

        except LockTimeoutError as exc:
            return GuardResult(
                success=(exc.exit_code == 0),
                mode=mode,
                message=str(exc),
                exit_code=exc.exit_code,
                actions_taken=actions,
            )
        except Exception as exc:  # noqa: BLE001 - phase failure is recorded, never raises
            return GuardResult(
                success=False,
                mode=mode,
                message=t("Error during the alignment operation: {error}", error=exc),
                exit_code=1,
                # The partial work matters most on failure: it says what
                # was already written before the cycle stopped.
                actions_taken=actions,
            )

    def _run_phases(self, actions: list[str], branch: str, skip_mcp: bool) -> list[str]:
        """Runs every write phase, each isolated from the others; returns the names that failed.

        They used to run as one chain, so a skill whose GitHub fetch timed out
        stopped the MCP render, the permission posture and the guardrail hook
        behind it, every 30 minutes, for as long as the network stayed down.
        None of them reads what another wrote, and each writes its own domain
        atomically, so there was no transaction to protect: only the safety
        controls that went unapplied. The failure is still a failure (the
        result is negative, the exit code non-zero, the doctor says which
        phases), it just does not take the rest down with it.
        """
        phases = (
            ("skills", lambda: self._phase_skills(actions)),
            ("mcp", lambda: self._phase_mcp(actions, skip_mcp)),
            ("permissions", lambda: self._phase_permissions(actions)),
            ("instructions", lambda: self._phase_instructions(actions)),
            ("launchers", lambda: self._phase_launchers(actions)),
            ("scheduler", lambda: self._phase_scheduler(actions, branch)),
            ("modules", lambda: self._phase_modules(actions)),
            # Last: it may spend minutes on pip, and the safety phases above do not wait for it.
            ("runtime", lambda: self._phase_runtime(actions)),
        )
        failed: list[str] = []
        for name, run_phase in phases:
            try:
                run_phase()
            except Exception as exc:  # noqa: BLE001 - one phase failing must not stop the others
                failed.append(name)
                actions.append(ERROR + t("Phase '{phase}' failed: {error}", phase=name, error=exc))
        return failed

    def _phase_git(
        self,
        mode: GuardMode,
        allow_offline: bool,
        auth_remote: str,
        branch: str,
        actions: list[str],
    ) -> GuardResult | None:
        """Inspects data git state and converges it (fast-forward / rebase /
        quarantine). Returns a result only when the cycle must stop here."""
        if mode == GuardMode.PREFLIGHT:
            return None
        # Auto-commit any pending tracked infra files so they don't block sync
        auto_ok, _auto_committed = auto_commit_infra_files(self.vault_data)
        if not auto_ok:
            # inspect_git_state below will still block fail-closed on DIRTY,
            # but the message must name the real cause (commit failed), not
            # just "unsaved changes".
            actions.append(WARN + t("Infra auto-commit failed; the Git inspection below decides whether the cycle can proceed"))
        untracked_infra = get_untracked_infra_files(self.vault_data)
        if untracked_infra:
            actions.append(t(
                "New infra files never committed ({count}): stage them with vault-push, or they stay local-only.",
                count=len(untracked_infra),
            ))

        git_status = inspect_git_state(
            self.vault_data,
            expected_branch=branch,
            remote=auth_remote,
            allow_offline=allow_offline,
        )
        if not git_status.allows_apply:
            return GuardResult(
                success=False,
                mode=mode,
                message=t("Operation blocked by Git: {reason}", reason=git_status.message),
                exit_code=1,
            )
        if git_status.state == GitState.BEHIND:
            ff_ok, ff_msg = fast_forward_merge(self.vault_data, remote=auth_remote, branch=branch)
            if not ff_ok:
                return GuardResult(
                    success=False,
                    mode=mode,
                    message=t("Error during automatic update: {reason}", reason=ff_msg),
                    exit_code=1,
                )
            actions.append(ff_msg)
        elif git_status.state == GitState.DIVERGED:
            rebase_res = run_git(self.vault_data, "rebase", f"{auth_remote}/{branch}")
            if rebase_res.returncode == 0:
                actions.append(t("Realigned with {remote}/{branch} via rebase", remote=auth_remote, branch=branch))
            else:
                run_git(self.vault_data, "rebase", "--abort")
                q_ok, _q_branch, q_msg = quarantine_diverged_commits(self.vault_data, remote=auth_remote, branch=branch)
                if q_ok:
                    actions.append(q_msg)
                else:
                    return GuardResult(
                        success=False,
                        mode=mode,
                        message=t("Error during divergence resolution: {reason}", reason=q_msg),
                        exit_code=1,
                    )
        elif git_status.state == GitState.FRESH:
            actions.append(t("Data state: {status}", status=git_status.message))
        elif git_status.state == GitState.AHEAD:
            actions.append(t("Data state: {status}", status=git_status.message))
        return None

    def _phase_preflight(self, mode: GuardMode) -> GuardResult | None:
        """Read-only validation of all configuration files. In PREFLIGHT
        mode the success result itself is the answer, so it comes back
        here instead of falling through to the write phases."""
        pf_ok, pf_msg = self.preflight()
        if not pf_ok:
            return GuardResult(success=False, mode=mode, message=pf_msg, exit_code=1)
        if mode == GuardMode.PREFLIGHT:
            return GuardResult(success=True, mode=mode, message=pf_msg, exit_code=0)
        return None

    def _phase_skills(self, actions: list[str]) -> None:
        """Skill materialization (skills.py)."""
        import os

        mat = SkillMaterializer(vault_data=self.vault_data, engine_root=self.engine_root, home=self.home)
        # The recurring guard never executes third-party installers: a Vault
        # commit could otherwise run arbitrary commands on every machine twice
        # an hour. Only an explicit `skills-sync apply` runs them.
        allow_exec = os.environ.get("NEXGEN_ALLOW_INSTALLER_EXEC", "").strip().lower() in ("1", "true", "yes")
        _skill_changes, skill_actions = mat.materialize(apply=True, allow_installer_exec=allow_exec)
        actions.extend(skill_actions)
        if any(is_error(action) for action in skill_actions):
            raise AlignmentError(t("Skill materialization failed (see the [ERROR] lines above)."))

    def _phase_mcp(self, actions: list[str], skip_mcp: bool) -> None:
        """MCP configuration rendering for the CLIs."""
        if skip_mcp:
            actions.append(t("MCP configurations not regenerated (explicitly requested)"))
            return
        try:
            from nexgen_core import mcp_trials

            expired = mcp_trials.purge_expired()
            if expired:
                actions.append(t("MCP trials that ran out were removed: {names}", names=", ".join(expired)))
        except Exception as exc:  # noqa: BLE001 - housekeeping must never stop the render
            actions.append(WARN + t("MCP trials could not be tidied: {error}", error=exc))
        try:
            rend = McpRenderer(vault_data=self.vault_data, engine_root=self.engine_root, home=self.home)
            results = rend.render_all(write=True)
        except Exception as exc:  # noqa: BLE001 - phase failure is recorded, never raises
            # A corrupt live config aborts the cycle here: skills already
            # wrote above, so the message must say the transaction is
            # partial instead of dying with a bare traceback.
            raise RuntimeError(t("MCP rendering failed ({error}): fix the CLI config and re-run", error=exc)) from exc
        actions.append(t("MCP configurations regenerated for every CLI"))
        failed = sorted(cli for cli, ok in results.items() if not ok)
        if failed:
            actions.append(WARN + t("MCP render reported no change applied for: {clis}", clis=", ".join(failed)))

    def _phase_permissions(self, actions: list[str]) -> None:
        """Permission posture + guardrail hook per CLI."""
        # Not wrapped: an unexpected error here means the guardrail may not have
        # been applied, which is a failed phase (run_phases records it), not a
        # warning. Unreadable policy files are reported as warnings by the call.
        actions.extend(self.apply_runtime_permissions())

    def _phase_instructions(self, actions: list[str]) -> None:
        """Instruction alignment, plus the Windows-only local-model adapter."""
        instr_actions = self.align_instructions()
        actions.extend(instr_actions)
        lm_actions = self.align_local_model_runtime()
        actions.extend(lm_actions)

    def _phase_launchers(self, actions: list[str]) -> None:
        """The commands themselves. A deleted or stale launcher
        after an update is drift like any other, and fixing it
        silently is the job: asking the user to do it isn't."""
        try:
            from nexgen_core.shims import install_shims

            # install_shims says what it rewrote. This used to hash every file in
            # ~/.local/bin before and after (829 MB read, twice, every 30 minutes
            # on a machine with three large CLIs there) to work out the same thing.
            repaired: list[str] = []
            install_shims(home=self.home, changed=repaired)
            if repaired:
                actions.append(t("Commands realigned ({count})", count=len(repaired)))
        except Exception as exc:  # noqa: BLE001 - phase failure is recorded, never raises
            actions.append(WARN + t("Commands not realigned: {error}", error=exc))

    def _phase_scheduler(self, actions: list[str], branch: str) -> None:
        """Startup self-alignment installation (systemd / scheduled task),
        plus the update notice lanes: shell hook and boot-time check are
        drift like any other, so the guard keeps them installed."""
        try:
            sched_ok = install_scheduler(
                home=self.home,
                engine_root=self.engine_root,
                vault_data=self.vault_data,
                vault=self.vault_data,
                branch=branch,
                log=lambda msg: actions.append(msg),
            )
            if sched_ok:
                actions.append(t("Startup self-alignment configured"))
            else:
                actions.append(WARN + t("Startup self-alignment reported no success and no error; verify with `nexgen doctor`"))
        except Exception as exc:  # noqa: BLE001 - phase failure is recorded, never raises
            actions.append(WARN + t("Self-alignment configuration did not succeed: {error}", error=exc))
        try:
            from nexgen_core.tools.update_notifier import ensure_boot_check, ensure_shell_hook

            actions.extend(ensure_shell_hook(self.home))
            actions.extend(ensure_boot_check(self.home))
        except Exception as exc:  # noqa: BLE001 - phase failure is recorded, never raises
            actions.append(WARN + t("Update notice lanes not ensured: {error}", error=exc))

    def _phase_modules(self, actions: list[str]) -> None:
        """Modules this machine declared: their commands and units
        are drift like any other. Doing it here is what turns the
        guard from something a module has to survive into what keeps
        it alive -- every primitive below is idempotent, so a module
        already in place costs nothing."""
        try:
            from nexgen_core.module_install import install_declared_modules
            from nexgen_core.modules import modules_state

            module_actions = install_declared_modules(
                modules_state(vault_data=self.vault_data, engine_root=self.engine_root),
                home=self.home,
                engine_root=self.engine_root,
                log=lambda msg: actions.append(msg),
            )
            if module_actions:
                actions.append(t("Modules realigned ({count})", count=len(module_actions)))
        except Exception as exc:  # noqa: BLE001 - phase failure is recorded, never raises
            actions.append(WARN + t("Modules not realigned: {error}", error=exc))

    def _phase_runtime(self, actions: list[str]) -> None:
        """The engine's own Python environment: provisioned from the checkout's
        dependency list, or, for a package install, only verified.

        Skipped where host mutations are disabled (sandboxes and tests): it creates a
        virtual environment and downloads packages.
        """
        from nexgen_core import runtime
        from nexgen_core.paths import installed_as_package, resolve_runtime_dir
        from nexgen_core.scheduler import host_mutations_disabled

        if installed_as_package():
            missing = runtime.missing_imports()
            if missing:
                raise runtime.RuntimeProvisionError(
                    t("The package environment cannot import: {modules}. Reinstall the engine.", modules=", ".join(missing))
                )
            return
        if host_mutations_disabled():
            actions.append(t("Engine environment not provisioned (host mutations are disabled)"))
            return
        checkout = self.engine_root.parent
        if not (checkout / "pyproject.toml").is_file():
            actions.append(WARN + t("Engine environment not provisioned: no pyproject.toml next to {path}", path=self.engine_root))
            return
        result = runtime.ensure_runtime(resolve_runtime_dir(self.home), checkout, log=actions.append)
        if result.changed:
            actions.append(t("Engine environment provisioned ({detail})", detail=result.detail))

    def _phase_liveness(
        self, actions: list[str], is_guard: bool, mode: GuardMode, failed: list[str] | None = None,
    ) -> None:
        """Liveness registration for the heartbeat (with the phases that failed, if any)."""
        if is_guard or mode == GuardMode.APPLY:
            warns = sum(1 for action in actions if is_warning(action))
            self.heartbeat.record_liveness(warnings=warns, failed_phases=failed or ())
            actions.append(t("Liveness recorded successfully"))
            self._refresh_update_cache_best_effort()

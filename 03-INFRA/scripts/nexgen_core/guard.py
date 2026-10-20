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

import logging
from pathlib import Path

from nexgen_core import guard_git, guard_instructions, guard_permissions
from nexgen_core.action_notes import ERROR, WARN, is_error, is_warning  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.beat import Heartbeat
from nexgen_core.errors import AlignmentError
from nexgen_core.config import load_mcp_manifest, load_skills_manifest
from nexgen_core.git_ops import get_current_branch, resolve_remotes, run_git
from nexgen_core.guard_instructions import _same_file  # noqa: F401 - kept importable from this module
from nexgen_core.guard_result import GuardMode, GuardResult
from nexgen_core.i18n import t
from nexgen_core.lock import LockTimeoutError, host_mutation
from nexgen_core.paths import (
    resolve_engine_root,
    resolve_home,
    resolve_state_dir,
    resolve_vault_data,
)
from nexgen_core.renderer import McpRenderer
from nexgen_core.scheduler import install_scheduler
from nexgen_core.skills import SkillMaterializer

logger = logging.getLogger(__name__)


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
        return guard_instructions.align_instructions(self)

    def _link_to_canonical(self, target: Path, canon: Path) -> tuple[bool, str | None]:
        """Points `target` at the canonical file; warns instead of losing it."""
        return guard_instructions._link_to_canonical(self, target, canon)

    def _align_opencode_instructions(self, canon: Path) -> str | None:
        """Points the OpenCode scope file at the canonical file."""
        return guard_instructions._align_opencode_instructions(self, canon)

    def _drop_dead_opencode_instructions_array(self) -> str | None:
        """Removes engine-added canonical entries from the dead V1 array."""
        return guard_instructions._drop_dead_opencode_instructions_array(self, McpRenderer)

    def align_local_model_runtime(self) -> list[str]:
        """Windows-only: relinks the private local-model-agent.ps1 adapter."""
        return guard_instructions.align_local_model_runtime(self)

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
        """Permission posture + guardrail hook for every installed CLI."""
        return guard_permissions.apply_runtime_permissions(self)

    def _event_sink_wanted(self) -> bool | None:
        """Whether a declared module needs the event-sink hook (None when unreadable)."""
        return guard_permissions._event_sink_wanted(self)

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

    def _phase_align(self, actions: list[str], skip_mcp: bool) -> list[str]:
        """First alignment block: skills + MCP rendering (moved from _run_phases)."""
        failed: list[str] = []
        for name, run_phase in (("skills", lambda: self._phase_skills(actions)),
                                ("mcp", lambda: self._phase_mcp(actions, skip_mcp))):
            try:
                outcome = run_phase()
            except Exception as exc:  # noqa: BLE001 - one phase failing must not stop the others
                outcome = [str(exc)]
            if outcome:
                failed.append(name)
                actions.append(ERROR + t("Phase '{phase}' failed: {error}", phase=name, error=", ".join(outcome)))
        return failed

    def _phase_run(self, actions: list[str], branch: str) -> list[str]:
        """Remaining run phases: instructions + launchers + scheduler + modules + runtime."""
        failed: list[str] = []
        for name, run_phase in (
            ("instructions", lambda: self._phase_instructions(actions)),
            ("launchers", lambda: self._phase_launchers(actions)),
            ("scheduler", lambda: self._phase_scheduler(actions, branch)),
            ("modules", lambda: self._phase_modules(actions)),
            # Last: it may spend minutes on pip, and the safety phases above do not wait for it.
            ("runtime", lambda: self._phase_runtime(actions)),
        ):
            try:
                run_phase()
            except Exception as exc:  # noqa: BLE001 - one phase failing must not stop the others
                failed.append(name)
                actions.append(ERROR + t("Phase '{phase}' failed: {error}", phase=name, error=exc))
        return failed

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
        failed: list[str] = []
        failed += self._phase_align(actions, skip_mcp)
        try:
            self._phase_permissions(actions)
        except Exception as exc:  # noqa: BLE001 - one phase failing must not stop the others
            failed.append("permissions")
            actions.append(ERROR + t("Phase '{phase}' failed: {error}", phase="permissions", error=exc))
        failed += self._phase_run(actions, branch)
        return failed

    def _phase_git(
        self,
        mode: GuardMode,
        allow_offline: bool,
        auth_remote: str,
        branch: str,
        actions: list[str],
    ) -> GuardResult | None:
        """Inspects data git state and converges it; a result only when it must stop."""
        return guard_git.converge_git_phase(self, mode, allow_offline, auth_remote, branch, actions)

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

    def _phase_mcp(self, actions: list[str], skip_mcp: bool) -> list[str]:
        """MCP configuration rendering for the CLIs; returns the CLIs it could not write."""
        if skip_mcp:
            actions.append(t("MCP configurations not regenerated (explicitly requested)"))
            return []
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
        failed = sorted(cli for cli, ok in results.items() if not ok)
        if not failed:
            actions.append(t("MCP configurations regenerated for every CLI"))
            return []
        # Success is claimed only when every target made it. The failing
        # ones are named with their error, so the line tells the user what
        # to fix rather than only that something went wrong.
        details = ", ".join(f"{cli} ({rend.last_errors[cli]})" if rend.last_errors.get(cli) else cli for cli in failed)
        actions.append(WARN + t("MCP render failed for: {clis}", clis=details))
        return failed

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
        from nexgen_core.shims import install_shims

        # install_shims says what it rewrote. This used to hash every file in
        # ~/.local/bin before and after (829 MB read, twice, every 30 minutes
        # on a machine with three large CLIs there) to work out the same thing.
        repaired: list[str] = []
        install_shims(home=self.home, changed=repaired)
        if repaired:
            actions.append(t("Commands realigned ({count})", count=len(repaired)))

    def _phase_scheduler(self, actions: list[str], branch: str) -> None:
        """Startup self-alignment installation (systemd / scheduled task),
        plus the update notice lanes: shell hook and boot-time check are
        drift like any other, so the guard keeps them installed."""
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
        from nexgen_core.tools.update_notifier import ensure_boot_check, ensure_shell_hook

        actions.extend(ensure_shell_hook(self.home))
        actions.extend(ensure_boot_check(self.home))

    def _phase_modules(self, actions: list[str]) -> None:
        """Modules this machine declared: their commands and units
        are drift like any other. Doing it here is what turns the
        guard from something a module has to survive into what keeps
        it alive -- every primitive below is idempotent, so a module
        already in place costs nothing."""
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

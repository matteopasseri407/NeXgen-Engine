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
import shutil
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from nexgen_core.beat import Heartbeat
from nexgen_core.config import load_mcp_manifest, load_skills_manifest
from nexgen_core.git_ops import (
    GitState,
    auto_commit_infra_files,
    fast_forward_merge,
    get_current_branch,
    inspect_git_state,
    quarantine_diverged_commits,
    resolve_remotes,
    run_git,
)
from nexgen_core.i18n import t
from nexgen_core.jsonc import parse_jsonc, set_jsonc_top_level_value
from nexgen_core.lock import HostLock, LockTimeoutError
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


def _launcher_fingerprints(home: Path) -> dict[str, int]:
    """Name and size of every launcher, to tell what actually changed."""
    bin_dir = home / ".local" / "bin"
    if not bin_dir.is_dir():
        return {}
    out: dict[str, int] = {}
    for entry in bin_dir.iterdir():
        try:
            out[entry.name] = entry.stat().st_size
        except OSError:
            continue
    return out


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
        """Read-only validation of all configuration files."""
        manifest_mcp = self.vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
        manifest_skills = self.vault_data / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"

        try:
            if manifest_mcp.is_file():
                load_mcp_manifest(manifest_mcp)
            if manifest_skills.is_file():
                load_skills_manifest(manifest_skills)
            return True, t("MCP and Skill configurations valid")
        except Exception as exc:
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
            # fine; making it disappear without a copy is not.
            if claude_md.is_file():
                backup = claude_md.with_name(
                    f"{claude_md.name}.pre-instructions-{time.strftime('%Y%m%d-%H%M%S')}.bak"
                )
                with contextlib.suppress(OSError):
                    shutil.copy2(claude_md, backup)
            claude_md.write_text(content, encoding="utf-8")
            actions.append(t("Updated instruction pointer {path}", path=claude_md))

        # The other three CLIs read the canonical file directly. Aligning
        # only one of them would mean having a canonical source for one
        # runtime and three stale copies for the others, which is the
        # opposite of the invariant.
        for label, target in (
            ("codex", self.home / ".codex" / "AGENTS.md"),
            ("antigravity", self.home / ".gemini" / "config" / "AGENTS.md"),
        ):
            if self._link_to_canonical(target, canon):
                actions.append(t("{label} instructions restored to canonical", label=label))

        opencode_action = self._align_opencode_instructions(canon)
        if opencode_action:
            actions.append(opencode_action)

        return actions

    def _link_to_canonical(self, target: Path, canon: Path) -> bool:
        """Points `target` at the canonical file. True if something had to change."""
        try:
            if target.is_symlink() and target.resolve() == canon.resolve():
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                # A real copy may contain hand-written lines.
                if target.is_file() and not target.is_symlink():
                    backup = target.with_name(
                        f"{target.name}.pre-instructions-{time.strftime('%Y%m%d-%H%M%S')}.bak"
                    )
                    with contextlib.suppress(OSError):
                        shutil.copy2(target, backup)
                target.unlink()
            try:
                target.symlink_to(canon)
            except OSError:
                # Windows without symlink privileges: a copy beats nothing.
                shutil.copy2(canon, target)
            return True
        except OSError:
            return False

    def _align_opencode_instructions(self, canon: Path) -> str | None:
        """Adds the canonical file to OpenCode's `instructions` list, without duplicating it.

        OpenCode doesn't read a file by convention: it reads whatever is
        declared to it. If the canonical file isn't in that list, that
        runtime is working without the policy every other runtime has.
        """
        for candidate in (
            self.home / ".config" / "opencode" / "opencode.jsonc",
            self.home / ".config" / "opencode" / "opencode.json",
        ):
            if not candidate.is_file():
                continue
            try:
                raw = candidate.read_text(encoding="utf-8")
                data = parse_jsonc(raw) if candidate.suffix == ".jsonc" else json.loads(raw or "{}")
            except (OSError, ValueError):
                return None
            if not isinstance(data, dict):
                return None

            declared = data.get("instructions")
            entries = [e for e in declared if isinstance(e, str)] if isinstance(declared, list) else []
            if any(_same_file(e, canon) for e in entries):
                return None

            entries.append(str(canon))
            data["instructions"] = entries
            try:
                if candidate.suffix == ".jsonc" and raw.strip():
                    body = set_jsonc_top_level_value(raw, "instructions", entries)
                else:
                    body = json.dumps(data, indent=2) + "\n"
                backup = candidate.with_name(
                    f"{candidate.name}.pre-instructions-{time.strftime('%Y%m%d-%H%M%S')}.bak"
                )
                with contextlib.suppress(OSError):
                    shutil.copy2(candidate, backup)
                candidate.write_text(body, encoding="utf-8")
            except OSError:
                return None
            return t("opencode instructions restored to canonical")
        return None

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
        if runtime.is_symlink() or runtime.exists():
            runtime.unlink(missing_ok=True)
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
                target.write_text(content, encoding="utf-8")
                actions.append(t("local-model: installed wrapper {name}", name=name))
        return actions

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
        if not manifest_path.is_file():
            return []
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            return ["[WARN] " + t("runtime-permissions: could not read {path} ({error})", path=manifest_path, error=exc)]
        if not isinstance(raw, dict):
            return ["[WARN] " + t("runtime-permissions: the root of {path} is not a map", path=manifest_path)]

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
            if not str(candidate).startswith(str(manifest_path.parent.resolve())):
                name = spec.get("name", spec["file"])
                return ["[WARN] " + t("runtime-permissions: {name} escapes permissions/, guardrail rejected", name=name)]
            if not candidate.is_file():
                return ["[WARN] " + t("runtime-permissions: guardrail body missing ({path})", path=candidate)]
            guardrail_source = candidate
            break

        engine_hooks_dir = self.engine_root / "agent-universal-layer" / "hooks"
        event_sink_source = engine_hooks_dir / "nexgen-event-sink.mjs"
        return apply_runtimes(
            home=self.home,
            engine_hooks_dir=engine_hooks_dir,
            posture=posture,
            guardrail_source=guardrail_source,
            event_sink_source=event_sink_source if event_sink_source.is_file() else None,
        )

    def run(
        self,
        mode: GuardMode = GuardMode.APPLY,
        allow_offline: bool = False,
        skip_mcp: bool = False,
    ) -> GuardResult:
        """Runs the requested cycle with locking and transactional safety."""
        is_guard = (mode == GuardMode.GUARD)
        actions: list[str] = []

        try:
            with HostLock(
                lock_path=self.state_dir / "agent-sync.lock",
                is_guard=is_guard,
                command_name=f"agent-sync-{mode.value}",
            ):
                # 1. Git inspection
                auth_remote, _ = resolve_remotes(self.vault_data)
                branch = get_current_branch(self.vault_data) or "main"

                if mode != GuardMode.PREFLIGHT:
                    # Auto-commit any pending tracked infra files so they don't block sync
                    auto_commit_infra_files(self.vault_data)

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
                            q_ok, q_branch, q_msg = quarantine_diverged_commits(self.vault_data, remote=auth_remote, branch=branch)
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

                # If this is a pull-only run, stop here
                if mode == GuardMode.PULL:
                    return GuardResult(success=True, mode=mode, message=t("Pull completed"), exit_code=0, actions_taken=actions)

                # 2. Preflight
                pf_ok, pf_msg = self.preflight()
                if not pf_ok:
                    return GuardResult(success=False, mode=mode, message=pf_msg, exit_code=1)

                if mode == GuardMode.PREFLIGHT:
                    return GuardResult(success=True, mode=mode, message=pf_msg, exit_code=0)

                # 3. Skill materialization
                mat = SkillMaterializer(vault_data=self.vault_data, engine_root=self.engine_root, home=self.home)
                skill_changes, skill_actions = mat.materialize(apply=True)
                actions.extend(skill_actions)

                # 4. MCP configuration rendering for the CLIs
                if skip_mcp:
                    actions.append(t("MCP configurations not regenerated (explicitly requested)"))
                else:
                    rend = McpRenderer(vault_data=self.vault_data, engine_root=self.engine_root, home=self.home)
                    rend.render_all(write=True)
                    actions.append(t("MCP configurations regenerated for every CLI"))

                # 4.6 Permission posture + guardrail hook per CLI
                try:
                    perm_actions = self.apply_runtime_permissions()
                    actions.extend(perm_actions)
                except Exception as exc:
                    actions.append("[WARN] " + t("runtime-permissions: phase skipped due to an unexpected error ({error})", error=exc))

                # 5. Instruction alignment
                instr_actions = self.align_instructions()
                actions.extend(instr_actions)

                # 5b. Local model adapter (Windows, bring-your-own)
                lm_actions = self.align_local_model_runtime()
                actions.extend(lm_actions)

                # 5c. The commands themselves. A deleted or stale launcher
                # after an update is drift like any other, and fixing it
                # silently is the job: asking the user to do it isn't.
                try:
                    from nexgen_core.shims import install_shims

                    before = _launcher_fingerprints(self.home)
                    install_shims(home=self.home)
                    repaired = sorted(_launcher_fingerprints(self.home).items() - before.items())
                    if repaired:
                        actions.append(t("Commands realigned ({count})", count=len(repaired)))
                except Exception as exc:
                    actions.append("[WARN] " + t("Commands not realigned: {error}", error=exc))

                # 6. Startup self-alignment installation (systemd / scheduled task)
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
                except Exception as exc:
                    actions.append("[WARN] " + t("Self-alignment configuration did not succeed: {error}", error=exc))

                # 7. Liveness registration for the heartbeat
                if is_guard or mode == GuardMode.APPLY:
                    self.heartbeat.record_liveness()
                    actions.append(t("Liveness recorded successfully"))

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
            )
        except Exception as exc:
            return GuardResult(
                success=False,
                mode=mode,
                message=t("Error during the alignment operation: {error}", error=exc),
                exit_code=1,
            )

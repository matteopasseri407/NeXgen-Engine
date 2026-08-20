"""Il ciclo di guardia (Guard) e sincronizzazione transazionale per NeXgen Engine v2.

Fasi del ciclo di guardia (guard / apply):
1. Lock host-wide (guard busy = exit 0, apply busy = exit 75)
2. Ispezione Git (verifica stato pulito, fetch dal remoto autoritativo)
3. Preflight (validazione configurazioni e schemi in sola lettura)
4. Materializzazione skill (skills.py)
5. Generazione configurazioni MCP (renderer.py)
6. Allineamento puntatori istruzioni (AGENTS.md -> CLAUDE.md / .gemini / .codex)
7. Esecuzione verifiche di allineamento
8. Registrazione liveness (agent-guard-liveness)
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from nexgen_core.beat import Heartbeat
from nexgen_core.config import load_mcp_manifest, load_skills_manifest
from nexgen_core.git_ops import (
    GitState,
    fast_forward_merge,
    get_current_branch,
    inspect_git_state,
    resolve_remotes,
)
from nexgen_core.lock import HostLock, LockTimeoutError
from nexgen_core.renderer import McpRenderer
from nexgen_core.scheduler import install_scheduler
from nexgen_core.skills import SkillMaterializer


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
    """Esecutore delle transazioni di sincronizzazione e guardia."""

    def __init__(
        self,
        vault_data: Path | None = None,
        engine_root: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self.home = home or Path.home()
        _v = vault_data or Path(os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH") or str(self.home / "KnowledgeVault"))
        self.vault_data = _v
        self.engine_root = engine_root or Path(os.environ.get("AGENT_ENGINE_ROOT") or str(self.home / ".nexgen-engine" / "03-INFRA"))
        self.heartbeat = Heartbeat(vault_data=self.vault_data, engine_root=self.engine_root)

    def preflight(self) -> tuple[bool, str]:
        """Validazione in sola lettura di tutti i file di configurazione."""
        manifest_mcp = self.vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
        manifest_skills = self.vault_data / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"

        try:
            if manifest_mcp.is_file():
                load_mcp_manifest(manifest_mcp)
            if manifest_skills.is_file():
                load_skills_manifest(manifest_skills)
            return True, "Configurazioni MCP e Skill valide"
        except Exception as exc:
            return False, f"Preflight fallito: {exc}"

    def align_instructions(self) -> list[str]:
        """Allinea i puntatori di compatibilità delle istruzioni (~/CLAUDE.md ecc.)."""
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
            claude_md.write_text(content, encoding="utf-8")
            actions.append(f"Aggiornato puntatore istruzioni {claude_md}")

        return actions

    def align_local_model_runtime(self) -> list[str]:
        """Windows-only: relinka l'adapter privato local-model-agent.ps1 (bring-your-own).

        Port del passo local_model_runtime della release: la vault puo'
        fornire un adapter privato (mai nel prodotto pubblico); se assente e'
        il default atteso, non un errore. Installa anche i wrapper stabili
        local-worker/local-agent.
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
            actions.append("local-model: relinkato local-model-agent.ps1")
        except OSError:
            shutil.copy2(src, runtime)
            actions.append("local-model: copiato local-model-agent.ps1")
        wrappers = {
            "local-worker.ps1": "$ScriptPath = Join-Path $PSScriptRoot 'local-model-agent.ps1'\r\n& $ScriptPath -Mode worker @args\r\n",
            "local-agent.ps1": "$ScriptPath = Join-Path $PSScriptRoot 'local-model-agent.ps1'\r\n& $ScriptPath -Mode agent @args\r\n",
        }
        for name, content in wrappers.items():
            target = local_bin / name
            if not target.is_file() or target.read_text(encoding="utf-8", errors="replace") != content:
                target.write_text(content, encoding="utf-8")
                actions.append(f"local-model: installato wrapper {name}")
        return actions

    def run(self, mode: GuardMode = GuardMode.APPLY, allow_offline: bool = False) -> GuardResult:
        """Esegue il ciclo richiesto con gestione lock e sicurezza transazionale."""
        is_guard = (mode == GuardMode.GUARD)
        actions: list[str] = []

        try:
            with HostLock(is_guard=is_guard, command_name=f"agent-sync-{mode.value}"):
                # 1. Ispezione Git
                auth_remote, _ = resolve_remotes(self.vault_data)
                branch = get_current_branch(self.vault_data) or "main"

                if mode != GuardMode.PREFLIGHT:
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
                            message=f"Operazione bloccata da Git: {git_status.message}",
                            exit_code=1,
                        )
                    if git_status.state == GitState.BEHIND:
                        ff_ok, ff_msg = fast_forward_merge(self.vault_data, remote=auth_remote, branch=branch)
                        if not ff_ok:
                            return GuardResult(
                                success=False,
                                mode=mode,
                                message=f"Errore durante l'aggiornamento automatico: {ff_msg}",
                                exit_code=1,
                            )
                        actions.append(ff_msg)
                    elif git_status.state == GitState.FRESH:
                        actions.append(f"Stato dati: {git_status.message}")
                    elif git_status.state == GitState.AHEAD:
                        actions.append(f"Stato dati: {git_status.message}")

                # Se la modalità è solo pull, ci fermiamo qui
                if mode == GuardMode.PULL:
                    return GuardResult(success=True, mode=mode, message="Pull completato", exit_code=0, actions_taken=actions)

                # 2. Preflight
                pf_ok, pf_msg = self.preflight()
                if not pf_ok:
                    return GuardResult(success=False, mode=mode, message=pf_msg, exit_code=1)

                if mode == GuardMode.PREFLIGHT:
                    return GuardResult(success=True, mode=mode, message=pf_msg, exit_code=0)

                # 3. Materializzazione skill
                mat = SkillMaterializer(vault_data=self.vault_data, engine_root=self.engine_root, home=self.home)
                skill_changes, skill_actions = mat.materialize(apply=True)
                actions.extend(skill_actions)

                # 4. Rendering configurazioni MCP per le CLI
                rend = McpRenderer(vault_data=self.vault_data, engine_root=self.engine_root, home=self.home)
                rend.render_all(write=True)
                actions.append("Configurazioni MCP rigenerate per tutte le CLI")

                # 5. Allineamento istruzioni
                instr_actions = self.align_instructions()
                actions.extend(instr_actions)

                # 5b. Adapter local model (Windows, bring-your-own)
                lm_actions = self.align_local_model_runtime()
                actions.extend(lm_actions)

                # 6. Installazione auto-allineamento all'avvio (systemd / scheduled task)
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
                        actions.append("Auto-allineamento all'avvio configurato")
                except Exception as exc:
                    actions.append(f"[WARN] Configurazione auto-allineamento non riuscita: {exc}")

                # 7. Registrazione liveness per il battito
                if is_guard or mode == GuardMode.APPLY:
                    self.heartbeat.record_liveness()
                    actions.append("Liveness registrata con successo")

                return GuardResult(
                    success=True,
                    mode=mode,
                    message="Allineamento completato con successo",
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
                message=f"Errore durante l'operazione di allineamento: {exc}",
                exit_code=1,
            )

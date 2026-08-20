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
    fast_forward_merge,
    get_current_branch,
    inspect_git_state,
    resolve_remotes,
)
from nexgen_core.jsonc import parse_jsonc, set_jsonc_top_level_value
from nexgen_core.lock import HostLock, LockTimeoutError
from nexgen_core.paths import resolve_engine_root, resolve_vault_data
from nexgen_core.renderer import McpRenderer
from nexgen_core.runtimes import apply_all as apply_runtimes
from nexgen_core.scheduler import install_scheduler
from nexgen_core.skills import SkillMaterializer


def _launcher_fingerprints(home: Path) -> dict[str, int]:
    """Nome e dimensione di ogni launcher, per dire cosa è cambiato davvero."""
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
        _v = resolve_vault_data(self.home, vault_data)
        self.vault_data = _v
        self.engine_root = resolve_engine_root(self.home, engine_root)
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
            # Il file può contenere righe scritte a mano. Rigenerarlo è giusto,
            # farlo sparire senza copia non lo è.
            if claude_md.is_file():
                backup = claude_md.with_name(
                    f"{claude_md.name}.pre-instructions-{time.strftime('%Y%m%d-%H%M%S')}.bak"
                )
                with contextlib.suppress(OSError):
                    shutil.copy2(claude_md, backup)
            claude_md.write_text(content, encoding="utf-8")
            actions.append(f"Aggiornato puntatore istruzioni {claude_md}")

        # Le altre tre CLI leggono il canonico direttamente. Allinearne una
        # sola significa avere una fonte canonica per un runtime e tre copie
        # ferme per gli altri, che è l'opposto dell'invariante.
        for label, target in (
            ("codex", self.home / ".codex" / "AGENTS.md"),
            ("antigravity", self.home / ".gemini" / "config" / "AGENTS.md"),
        ):
            if self._link_to_canonical(target, canon):
                actions.append(f"Istruzioni di {label} riportate al canonico")

        opencode_action = self._align_opencode_instructions(canon)
        if opencode_action:
            actions.append(opencode_action)

        return actions

    def _link_to_canonical(self, target: Path, canon: Path) -> bool:
        """Fa puntare `target` al file canonico. Vero se ha dovuto cambiare qualcosa."""
        try:
            if target.is_symlink() and target.resolve() == canon.resolve():
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                # Una copia reale può contenere righe scritte a mano.
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
                # Windows senza privilegi di symlink: una copia è meglio del nulla.
                shutil.copy2(canon, target)
            return True
        except OSError:
            return False

    def _align_opencode_instructions(self, canon: Path) -> str | None:
        """Aggiunge il canonico all'elenco `instructions` di OpenCode, senza duplicarlo.

        OpenCode non legge un file per convenzione: legge quelli che gli si
        dichiarano. Se il canonico non è in quell'elenco, quel runtime sta
        lavorando senza la politica che tutti gli altri hanno.
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
            entries = list(declared) if isinstance(declared, list) else []
            if str(canon) in entries:
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
            return "Istruzioni di opencode riportate al canonico"
        return None

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

    def apply_runtime_permissions(self) -> list[str]:
        """Postura dei permessi + hook guardrail per ogni CLI installata.

        La POLICY -- quale postura, quale corpo di guardrail -- e' dato
        privato del Vault (03-INFRA/agent-universal-layer/permissions/
        manifest.yaml), mai dell'engine pubblico: senza quel file questa
        fase e' un no-op completo, cosi' nessun utente finale eredita la
        postura permessi di qualcun altro. Il meccanismo che la applica vive
        in nexgen_core.runtimes; qui si legge solo il manifest e si traduce
        in argomenti semplici per quel meccanismo.
        """
        manifest_path = self.vault_data / "03-INFRA" / "agent-universal-layer" / "permissions" / "manifest.yaml"
        if not manifest_path.is_file():
            return []
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            return [f"[WARN] runtime-permissions: impossibile leggere {manifest_path} ({exc})"]
        if not isinstance(raw, dict):
            return [f"[WARN] runtime-permissions: la radice di {manifest_path} non è una mappa"]

        posture = {
            cli: value
            for cli, value in (raw.get("posture") or {}).items()
            if isinstance(cli, str) and isinstance(value, str)
        }

        # Una sola policy di guardrail e' supportata (il primo hook
        # dichiarato): e' l'unico caso reale, e generalizzare a un elenco
        # arbitrario di eventi/matcher per CLI e' esattamente la complessita'
        # a cinque mappe che questo pacchetto sostituisce.
        guardrail_source: Path | None = None
        for spec in raw.get("hooks") or []:
            if not isinstance(spec, dict) or not isinstance(spec.get("file"), str):
                continue
            candidate = (manifest_path.parent / spec["file"]).resolve()
            if not str(candidate).startswith(str(manifest_path.parent.resolve())):
                name = spec.get("name", spec["file"])
                return [f"[WARN] runtime-permissions: {name} esce da permissions/, guardrail rifiutato"]
            if not candidate.is_file():
                return [f"[WARN] runtime-permissions: corpo del guardrail mancante ({candidate})"]
            guardrail_source = candidate
            break

        engine_hooks_dir = self.engine_root / "agent-universal-layer" / "hooks"
        return apply_runtimes(
            home=self.home,
            engine_hooks_dir=engine_hooks_dir,
            posture=posture,
            guardrail_source=guardrail_source,
        )

    def run(
        self,
        mode: GuardMode = GuardMode.APPLY,
        allow_offline: bool = False,
        skip_mcp: bool = False,
    ) -> GuardResult:
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
                if skip_mcp:
                    actions.append("Configurazioni MCP non rigenerate (richiesto esplicitamente)")
                else:
                    rend = McpRenderer(vault_data=self.vault_data, engine_root=self.engine_root, home=self.home)
                    rend.render_all(write=True)
                    actions.append("Configurazioni MCP rigenerate per tutte le CLI")

                # 4.6 Postura dei permessi + hook guardrail per CLI
                try:
                    perm_actions = self.apply_runtime_permissions()
                    actions.extend(perm_actions)
                except Exception as exc:
                    actions.append(f"[WARN] runtime-permissions: fase saltata per errore imprevisto ({exc})")

                # 5. Allineamento istruzioni
                instr_actions = self.align_instructions()
                actions.extend(instr_actions)

                # 5b. Adapter local model (Windows, bring-your-own)
                lm_actions = self.align_local_model_runtime()
                actions.extend(lm_actions)

                # 5c. I comandi stessi. Un launcher cancellato o rimasto indietro
                # dopo un aggiornamento è deriva come le altre, e riparare in
                # silenzio è il mestiere: chiederlo all'utente non lo è.
                try:
                    from nexgen_core.shims import install_shims

                    before = _launcher_fingerprints(self.home)
                    install_shims(home=self.home)
                    repaired = sorted(_launcher_fingerprints(self.home).items() - before.items())
                    if repaired:
                        actions.append(f"Comandi riallineati ({len(repaired)})")
                except Exception as exc:
                    actions.append(f"[WARN] Comandi non riallineati: {exc}")

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

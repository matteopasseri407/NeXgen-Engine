"""Operazioni Git sicure e gestione dei remote per NeXgen Engine v2.

Regole di sicurezza non negoziabili:
1. Un albero di lavoro con modifiche utente non salvate (dirty) non viene MAI toccato.
2. In caso di conflitto o rebase in corso, l'operazione si blocca immediatamente e indica il comando di abort.
3. Il push autoritativo usa fetch + verifica divergenza, con rebase pulito prima della pubblicazione.
4. I mirror configurati vengono aggiornati dopo il remoto principale; il loro eventuale fallimento non invalida il primario.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml


class GitState(str, Enum):
    FRESH = "fresh"
    LOCAL_ONLY = "local_only"
    WRONG_BRANCH = "wrong_branch"
    CONFLICTED = "conflicted"
    DIRTY = "dirty"
    REMOTE_MISSING = "remote_missing"
    FETCH_FAILED = "fetch_failed"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    ERROR = "error"


@dataclass(frozen=True)
class GitStatusResult:
    state: GitState
    message: str
    branch: str = ""
    uncommitted_files: list[str] = field(default_factory=list)

    @property
    def allows_apply(self) -> bool:
        return self.state in {GitState.FRESH, GitState.LOCAL_ONLY, GitState.AHEAD, GitState.BEHIND}


def fast_forward_merge(repo_dir: Path, remote: str = "origin", branch: str = "main") -> tuple[bool, str]:
    """Esegue un merge fast-forward (--ff-only) sicuro quando lo stato è BEHIND."""
    res = run_git(repo_dir, "merge", "--ff-only", f"{remote}/{branch}")
    if res.returncode == 0:
        return True, f"Dati aggiornati con successo tramite fast-forward da {remote}/{branch}"
    return False, f"Fast-forward fallito: {res.stderr.strip()}"


def run_git(repo_dir: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Esecuzione sicura di un comando git con timeout e cattura output."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))


def resolve_remotes(vault_data: Path) -> tuple[str, list[str]]:
    """Risolve il remoto autoritativo e i mirror da environment o da sync/remotes.yaml."""
    env_remote = os.environ.get("KNOWLEDGE_VAULT_REMOTE")
    env_mirrors = os.environ.get("KNOWLEDGE_VAULT_MIRRORS")

    auth_remote = "origin"
    mirrors: list[str] = []

    remotes_file = vault_data / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml"
    if remotes_file.is_file():
        try:
            data = yaml.safe_load(remotes_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                auth_remote = str(data.get("authoritative_remote", "origin"))
                raw_mirrors = data.get("mirrors", [])
                if isinstance(raw_mirrors, list):
                    mirrors = [str(m) for m in raw_mirrors if str(m).strip()]
        except Exception:
            pass

    # Override da variabili d'ambiente
    if env_remote:
        auth_remote = env_remote.strip()
    if env_mirrors:
        mirrors = [m.strip() for m in env_mirrors.split(",") if m.strip()]

    return auth_remote, mirrors


def get_current_branch(repo_dir: Path) -> str:
    """Restituisce il nome del branch corrente o stringa vuota se detached."""
    r = run_git(repo_dir, "symbolic-ref", "--quiet", "--short", "HEAD")
    if r.returncode == 0:
        return r.stdout.strip()
    return ""


def get_uncommitted_files(repo_dir: Path) -> list[str]:
    """I file tracciati con modifiche non ancora committate.

    I file non tracciati restano fuori di proposito: un file nuovo che nessuno
    ha ancora messo in stage non è lavoro in pericolo, e trattarlo come tale
    bloccherebbe il ciclo su ogni scarabocchio lasciato nella cartella.
    """
    r = run_git(repo_dir, "status", "--porcelain", "--untracked-files=no")
    if r.returncode != 0 or not r.stdout.strip():
        return []
    files: list[str] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if len(line) > 3:
            files.append(line[3:].strip())
    return files


def check_conflicts_or_rebase(repo_dir: Path) -> str | None:
    """Verifica se ci sono rebase o merge in corso."""
    git_dir_r = run_git(repo_dir, "rev-parse", "--git-dir")
    if git_dir_r.returncode != 0:
        return None
    git_dir = Path(git_dir_r.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo_dir / git_dir

    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        return "un rebase git è in corso nel vault: esegui 'git rebase --abort' prima di procedere"
    if (git_dir / "MERGE_HEAD").exists():
        return "un merge git è in corso nel vault: esegui 'git merge --abort' prima di procedere"
    return None


def inspect_git_state(
    repo_dir: Path,
    expected_branch: str = "main",
    remote: str = "origin",
    allow_offline: bool = False
) -> GitStatusResult:
    """Ispezione completa dello stato Git secondo il contratto transazionale."""
    if remote in ("local", "none"):
        return GitStatusResult(GitState.LOCAL_ONLY, "Modalità Local-Only", expected_branch)

    # 1. Conflitti o operazioni pendenti
    conflict_msg = check_conflicts_or_rebase(repo_dir)
    if conflict_msg:
        return GitStatusResult(GitState.CONFLICTED, conflict_msg, expected_branch)

    # 2. Verifica branch
    curr_branch = get_current_branch(repo_dir)
    if not curr_branch or curr_branch != expected_branch:
        found = curr_branch or "HEAD staccato (detached)"
        return GitStatusResult(
            GitState.WRONG_BRANCH,
            f"Branch corrente '{found}', atteso '{expected_branch}'",
            curr_branch
        )

    # 3. File non committati (dirty)
    uncommitted = get_uncommitted_files(repo_dir)
    if uncommitted:
        return GitStatusResult(
            GitState.DIRTY,
            f"Modifiche non salvate su {len(uncommitted)} file tracciati",
            curr_branch,
            uncommitted
        )

    # 4. Verifica esistenza remote
    if run_git(repo_dir, "remote", "get-url", remote).returncode != 0:
        if allow_offline:
            return GitStatusResult(GitState.FRESH, "Offline consentito manualmente (remoto assente)", curr_branch)
        return GitStatusResult(
            GitState.REMOTE_MISSING,
            f"Remoto autoritativo '{remote}' non configurato",
            curr_branch
        )

    # 5. Fetch dal remote
    fetch_res = run_git(repo_dir, "fetch", "--prune", remote, expected_branch)
    if fetch_res.returncode != 0:
        if allow_offline:
            return GitStatusResult(GitState.FRESH, "Offline consentito manualmente", curr_branch)
        detail = (fetch_res.stderr or fetch_res.stdout).strip()
        msg = f"Impossibile raggiungere il remoto {remote}/{expected_branch}" + (f": {detail}" if detail else "")
        return GitStatusResult(GitState.FETCH_FAILED, msg, curr_branch)

    # 6. Confronto commit tra locale e remoto
    lh_res = run_git(repo_dir, "rev-parse", expected_branch)
    rh_res = run_git(repo_dir, "rev-parse", f"{remote}/{expected_branch}")

    if lh_res.returncode != 0:
        return GitStatusResult(GitState.ERROR, f"Branch locale '{expected_branch}' non trovato", curr_branch)

    # Se il branch remoto non esiste ancora (es. primo push su repository nuovo)
    if rh_res.returncode != 0:
        return GitStatusResult(GitState.AHEAD, f"Il branch locale '{expected_branch}' non è ancora presente su {remote}", curr_branch)

    mb_res = run_git(repo_dir, "merge-base", expected_branch, f"{remote}/{expected_branch}")
    if mb_res.returncode != 0:
        return GitStatusResult(
            GitState.ERROR,
            f"Errore nel calcolo merge-base con {remote}/{expected_branch}",
            curr_branch
        )

    lh, rh, mb = lh_res.stdout.strip(), rh_res.stdout.strip(), mb_res.stdout.strip()

    if lh == rh:
        return GitStatusResult(GitState.FRESH, "Dati già allineati", curr_branch)
    elif mb == lh:
        return GitStatusResult(GitState.BEHIND, f"Il remoto {remote} ha nuovi commit (aggiornamento disponibile)", curr_branch)
    elif mb == rh:
        return GitStatusResult(GitState.AHEAD, f"Il branch locale ha commit non ancora inviati a {remote}", curr_branch)
    else:
        return GitStatusResult(GitState.DIVERGED, f"Il branch locale è divergente rispetto a {remote} (risoluzione manuale richiesta)", curr_branch)


def publish_changes(
    repo_dir: Path,
    branch: str = "main",
    remote: str = "origin",
    mirrors: list[str] | None = None,
    commit_msg: str | None = None,
    files_to_commit: list[str] | None = None
) -> tuple[bool, str]:
    """Committa il lavoro e lo pubblica sul remoto autoritativo e sui mirror.

    Il commit locale avviene sempre, anche in modalità Local-Only: là non c'è
    un remoto verso cui spingere, ma il lavoro va comunque messo al sicuro
    nella storia. Saltare anche il commit significa restituire "fatto" a chi
    non ha più il proprio lavoro da nessuna parte.
    """
    committed = False

    # Il commit avviene per primo, prima di qualunque considerazione sul remoto.
    if commit_msg:
        if files_to_commit:
            add_res = run_git(repo_dir, "add", "--", *files_to_commit)
            if add_res.returncode != 0:
                return False, f"git add fallito: {add_res.stderr}"

        # Verifica se ci sono modifiche in staging da committare
        staged_check = run_git(repo_dir, "diff", "--cached", "--quiet")
        if staged_check.returncode != 0:
            c_res = run_git(repo_dir, "commit", "-m", commit_msg)
            if c_res.returncode != 0:
                return False, f"git commit fallito: {c_res.stderr}"
            committed = True

    if remote in ("local", "none"):
        if committed:
            return True, "Commit locale eseguito (Modalità Local-Only: nessun remoto da aggiornare)"
        return True, "Niente da committare (Modalità Local-Only: nessun remoto da aggiornare)"

    # Fetch e verifica prima del push
    fetch_res = run_git(repo_dir, "fetch", "--prune", remote, branch)
    if fetch_res.returncode != 0:
        if committed:
            return False, (
                f"{remote} non raggiungibile: il commit resta in locale, "
                f"ripubblicalo più tardi con 'vault-push'"
            )
        return False, f"Impossibile raggiungere {remote} per la pubblicazione"

    lh = run_git(repo_dir, "rev-parse", branch).stdout.strip()
    rh = run_git(repo_dir, "rev-parse", f"{remote}/{branch}").stdout.strip()
    mb = run_git(repo_dir, "merge-base", branch, f"{remote}/{branch}").stdout.strip()

    if lh != rh:
        if mb == rh:
            # Siamo avanti: push normale
            p_res = run_git(repo_dir, "push", remote, branch)
            if p_res.returncode != 0:
                return False, f"Push su {remote} fallito: {p_res.stderr}"
        elif mb == lh:
            return False, f"Impossibile inviare: il branch locale è indietro rispetto a {remote}"
        else:
            # Divergenza. Un rebase automatico su un albero con modifiche non
            # committate mette in gioco lavoro che nessuno ci ha affidato:
            # meglio fermarsi e dire quali file lo impediscono.
            dirty = get_uncommitted_files(repo_dir)
            if dirty:
                shown = ", ".join(dirty[:5]) + ("..." if len(dirty) > 5 else "")
                return False, (
                    f"Dati divergenti rispetto a {remote}, e ci sono modifiche non committate "
                    f"({shown}). Non riallineo da solo: committale o mettile da parte, poi riprova"
                )
            rebase_res = run_git(repo_dir, "rebase", f"{remote}/{branch}")
            if rebase_res.returncode == 0:
                p_res = run_git(repo_dir, "push", remote, branch)
                if p_res.returncode != 0:
                    return False, f"Push dopo rebase fallito: {p_res.stderr}"
            else:
                run_git(repo_dir, "rebase", "--abort")
                return False, f"Dati divergenti rispetto a {remote}, rebase automatico non riuscito"

    # Aggiornamento mirror (best effort)
    if mirrors:
        for mirror in mirrors:
            m_res = run_git(repo_dir, "push", mirror, branch)
            if m_res.returncode != 0:
                # Tentativo con force-with-lease dopo fetch
                run_git(repo_dir, "fetch", "--prune", mirror, branch)
                run_git(repo_dir, "push", "--force-with-lease", mirror, branch)

    return True, "Pubblicazione completata con successo"

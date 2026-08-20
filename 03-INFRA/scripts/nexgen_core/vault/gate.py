"""Il cancello di sicurezza di vault-groom.

Ogni funzione qui applica una garanzia strutturale, non una convenzione
sperabile:

- `require_clean_tree`: con modifiche non committate non si parte -- il
  cancello ha bisogno di un HEAD pulito da cui clonare.
- `prepare_clone`: clona il vault e rimuove SUBITO il remote `origin` dal
  clone, così un `git push` dal write pass non ha dove andare, non solo un
  divieto scritto nel prompt.
- `confirm_tranche`: richiede la parola "yes" per esteso, confronto
  case-sensitive; qualunque altra risposta annulla senza scrivere nulla.
- `hash_bytes` / `verify_hash_unchanged`: la guardia anti-TOCTOU. Il piano
  approvato viene hashato subito dopo l'approvazione e ri-hashato appena
  prima del write pass -- se è cambiato nel frattempo, si aborta.

`hash_bytes` normalizza CRLF -> LF prima di calcolare lo sha256: lo stesso
testo scritto e riletto su Windows produrrebbe altrimenti un hash diverso
da quello calcolato altrove, per un file che è testo per un umano/LLM, non
un blob binario.
"""
from __future__ import annotations

import hashlib
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from nexgen_core.git_ops import run_git
from nexgen_core.vault.git_utils import git

CLONE_TIMEOUT_SECONDS = 300


class GateError(RuntimeError):
    """Una regola di sicurezza non negoziabile non è soddisfatta."""


def working_tree_is_clean(repo: Path) -> bool:
    """True se `git status --porcelain` non riporta nulla."""
    result = run_git(repo, "status", "--porcelain")
    return result.returncode == 0 and result.stdout.strip() == ""


def require_clean_tree(repo: Path) -> None:
    """Regola 5: un albero sporco non deve mai arrivare al clone."""
    if not working_tree_is_clean(repo):
        raise GateError(
            f"vault-groom: the vault's working tree is not clean ({repo}) -- "
            "commit or stash them first. Aborting: the temp-clone gate needs "
            "a clean HEAD to clone from, zero writes made."
        )


def hash_bytes(data: bytes) -> str:
    """sha256 dei byte, dopo normalizzazione CRLF -> LF."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def write_plan_record(state_dir: Path, timestamp: str, tranche_text: str) -> Path:
    """Salva il testo della tranche approvata come unica fonte di verità.

    L'hash che la conferma mostra e che la guardia TOCTOU ri-calcola è
    sempre quello dei BYTE di questo file, mai della stringa in memoria --
    così i due controlli e il write pass concordano su cosa stanno hashando.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    plan_record = state_dir / f"{timestamp}-plan.txt"
    text = tranche_text if tranche_text.endswith("\n") else tranche_text + "\n"
    plan_record.write_text(text, encoding="utf-8", newline="\n")
    return plan_record


def hash_plan_record(plan_record: Path) -> str:
    """L'hash del file su disco, non della stringa che lo ha generato."""
    return hash_bytes(plan_record.read_bytes())


def verify_hash_unchanged(plan_record: Path, expected_hash: str) -> None:
    """Regola 4: il TOCTOU guard, ri-eseguito appena prima del write pass."""
    current = hash_plan_record(plan_record)
    if current != expected_hash:
        raise GateError(
            f"vault-groom: plan record changed after approval (expected "
            f"{expected_hash}, got {current}) -- aborting, zero writes. "
            "Re-run and re-approve."
        )


def confirm_tranche(
    tranche_text: str,
    tranche_hash: str,
    *,
    input_func: Callable[[str], str] = input,
    output: Callable[[str], object] = print,
) -> bool:
    """Regola 3: mostra la tranche e richiede 'yes' per esteso, esatto."""
    output("=" * 70)
    output(f" Proposed tranche (sha256 {tranche_hash[:12]}...) -- read it before confirming")
    output("=" * 70)
    output(tranche_text)
    output("=" * 70)
    output("Type exactly 'yes' to execute THIS tranche as-is.")
    output("Any other answer cancels: no changes to the vault.")
    try:
        answer = input_func("Proceed? > ")
    except EOFError:
        answer = ""
    return answer == "yes"


def prepare_clone(vault: Path, state_dir: Path, timestamp: str) -> Path:
    """Regola 2: il temp-clone gate.

    Clona il vault in una directory temporanea sotto `state_dir` e rimuove
    SUBITO il remote `origin` dal clone, prima che il write pass esegua un
    solo comando -- è questo, non il wording del prompt, a rendere un
    `git push` dal write pass meccanicamente impossibile.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    clone_dir = Path(tempfile.mkdtemp(prefix=f"{timestamp}-clone-", dir=str(state_dir)))
    subprocess.run(
        ["git", "clone", "-q", str(vault), str(clone_dir)],
        check=True,
        timeout=CLONE_TIMEOUT_SECONDS,
    )
    git(clone_dir, "remote", "remove", "origin")
    return clone_dir

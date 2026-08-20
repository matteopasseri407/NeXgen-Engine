"""Il registro degli adattatori CLI + l'unico punto di integrazione col guard.

`apply_all()` e' la fase che il ciclo di guardia chiama: riceve la postura e
il corpo del guardrail gia' RISOLTI (nessun parsing di manifest qui dentro --
quello resta compito del chiamante, guard.py, che e' anche l'unico posto che
sa dov'e' il Vault) e ritorna una lista di azioni testuali. Non stampa nulla:
la formattazione e' del chiamante.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.runtimes.antigravity import AntigravityRuntime
from nexgen_core.runtimes.base import GuardrailError, Runtime
from nexgen_core.runtimes.claude import ClaudeRuntime
from nexgen_core.runtimes.codex import CodexRuntime
from nexgen_core.runtimes.opencode import OpenCodeRuntime

#: nome -> istanza. Aggiungere una quinta CLI e' aggiungere un file
#: runtimes/<nome>.py con la sua classe e una riga qui, mai toccare le altre.
REGISTRY: dict[str, Runtime] = {
    "claude": ClaudeRuntime(),
    "codex": CodexRuntime(),
    "opencode": OpenCodeRuntime(),
    "antigravity": AntigravityRuntime(),
}


def apply_all(
    *,
    home: Path,
    engine_hooks_dir: Path,
    posture: dict[str, str] | None = None,
    guardrail_source: Path | None = None,
) -> list[str]:
    """Applica postura + guardrail hook su ogni CLI installata.

    Ordine per CLI, non negoziabile: guardrail PRIMA, postura DOPO. Una
    postura che toglie i prompt non deve mai raggiungere il disco se il suo
    guardrail dichiarato ha appena fallito l'installazione -- e' esattamente
    l'incidente che questo ordine previene.

    Una CLI non installata non e' un guasto: si salta in silenzio. Un
    guastro REALE durante l'installazione del guardrail (config corrotta,
    percorso non sicuro) blocca SOLO la postura di quella CLI per questo
    giro, mai le altre.
    """
    posture = posture or {}
    actions: list[str] = []

    for runtime in REGISTRY.values():
        if not runtime.is_installed(home):
            continue

        guardrail_ok = True
        if guardrail_source is not None:
            try:
                result = runtime.install_guardrail(home, guardrail_source, engine_hooks_dir)
            except GuardrailError as exc:
                actions.append(f"[WARN] {runtime.name}: installazione guardrail rifiutata ({exc})")
                guardrail_ok = False
            else:
                if result:
                    actions.append(result)

        desired_posture = posture.get(runtime.name)
        if not desired_posture:
            continue
        if not guardrail_ok:
            actions.append(
                f"[WARN] {runtime.name}: postura '{desired_posture}' NON applicata -- "
                "il suo guardrail dichiarato non si e' installato correttamente"
            )
            continue
        try:
            result = runtime.apply_posture(home, desired_posture)
        except GuardrailError as exc:
            actions.append(f"[WARN] {runtime.name}: applicazione postura rifiutata ({exc})")
        else:
            if result:
                actions.append(result)

    return actions

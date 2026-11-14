"""nexgen: one-job reason="generate pointers and instructions for supervision executor prompts and reviews"
Generates the prompt pointer text for unit execution and correction reviews.
The brief and review files remain the canonical source on disk; prompts
only reference their paths rather than duplicating their contents.
"""
from __future__ import annotations

from pathlib import Path


def corrections_text(task_id: str, unit: str, review: Path, round_no: int) -> str:
    """A pointer to the review round, never its text: the file is the one place it is read from."""
    brief = review.parent / f"20-brief-{unit}.md"
    return (
        f"Sei l'esecutore dell'unità {unit} del compito {task_id}, giro di correzione {round_no}. "
        f"Leggi le correzioni in {review} e il brief in {brief}, applica solo quelle. "
        "Lavora in questa copia, committa sul ramo locale, aggiorna il resoconto. Niente push."
    )


def prompt_text(task_id: str, unit: str, brief: Path, *, harness: str = "opencode") -> str:
    """A pointer to the brief, never the brief: the file stays the one place it is read from.

    A local unit gets two extra sentences: its brief is a step-by-step plan because
    the local model executes instead of designing, and the acceptance tests are
    already written red by somebody else, so touching them is not its work.
    """
    report = brief.parent / f"30-resoconto-{unit}.md"
    text = (
        f"Sei l'esecutore dell'unità {unit} del compito {task_id}. "
        f"Leggi il brief in {brief} e segui solo quello. "
        f"Lavora in questa copia, committa sul ramo locale, scrivi il resoconto in {report}. "
        "Niente push."
    )
    if harness == "local":
        text += (
            " Il brief è un piano passo per passo: esegui i passi nell'ordine, non riprogettare. "
            "I test di accettazione sono già scritti e rossi: non modificarli, fai passare quelli."
        )
    return text


def model_note(requested: str, argv: list[str]) -> dict:
    """What the owner asked for and whether it reached the command line, read from the argv itself."""
    if not requested:
        return {}
    applied = requested in argv or any(arg.startswith(f"{requested}-") for arg in argv)
    return {"model_requested": requested, "model_applied": applied}

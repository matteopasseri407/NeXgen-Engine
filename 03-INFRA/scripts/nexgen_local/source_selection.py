"""Source selection and content normalization, independent of orchestration."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

from .config import LaneConfig

STOPWORDS = frozenset(
    {
        "di",
        "a",
        "da",
        "in",
        "con",
        "su",
        "per",
        "tra",
        "fra",
        "il",
        "lo",
        "la",
        "i",
        "gli",
        "le",
        "un",
        "uno",
        "una",
        "che",
        "e",
        "ed",
        "del",
        "della",
        "dei",
        "delle",
        "degli",
        "nel",
        "nella",
        "nei",
        "nelle",
        "al",
        "alla",
        "ai",
        "alle",
        "dal",
        "dalla",
        "dai",
        "dalle",
        "sul",
        "sulla",
        "sono",
        "come",
        "cosa",
        "mi",
        "ti",
        "si",
        "se",
        "non",
        "piu",
        "ma",
        "anche",
        "ho",
        "hai",
        "ha",
        "questo",
        "questa",
        "quel",
        "quella",
        "quale",
        "quali",
        "dove",
        "quando",
        "senza",
        "dillo",
        "dimmelo",
        "trova",
        "cerca",
        "leggi",
        "riassumi",
        "apri",
        "restituisci",
        "rispondi",
        "italiano",
        "conciso",
        "riga",
        "due",
        "parole",
        "solo",
        "progetto",
        "file",
        "nota",
        "vault",
        "knowledgevault",
        "repository",
        "locale",
    }
)


PATH_RE = re.compile(r"`?([\w./\\:-]+\.(?:md|pdf|txt|ya?ml|json|py|toml|sh|ps1|cfg|ini))`?")  # \\: = assoluti Windows


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", flags=re.S)


_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")


#: A line that speaks to the assistant and tells it to drop what the user asked or to answer a fixed string,
#: the shape of every injection that has ever been planted in a note, a PDF or a search result. It needs BOTH
#: an addressee and an override in the same line, so an ordinary note ("ignora i file temporanei", "il modello
#: di contratto") is left alone.
_ADDRESSEE = r"(?:assistent[ei]|assistant|chatbot|\bllm\b|\bmodello\b|\bmodel\b|\bia\b)"
_OVERRIDE = (
    r"(?:\bignora\w*|\bignore\b|\bdisregard\b|\bdimentica\w*|\bforget\b|\boverride\b|non\s+seguire|do\s+not\s+follow"
    r"|rispondi\s+(?:solo|soltanto|esclusivamente)\s+con|(?:reply|respond|answer)\s+(?:only|exactly)\s+with)"
)
_ADDRESSED_OVERRIDE_RE = re.compile(rf"(?im)^(?=.*{_ADDRESSEE})(?=.*{_OVERRIDE}).*$")
#: The same thing said without naming the addressee: the classic "ignore the previous instructions" and an
#: order about what the final answer must say, in Italian and English. Narrow on purpose: "rispondi solo
#: con un si o un no" is something a person writes in an ordinary note.
_CLASSIC_OVERRIDE_RE = re.compile(
    r"(?im)^.*(?:"
    r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier|your)\s+(?:instructions|rules|prompt)\b"
    r"|\b(?:ignora|dimentica|scorda)\w*\s+(?:tutte\s+)?(?:le\s+)?(?:istruzioni|regole|indicazioni)\s+(?:precedenti|sopra|date|iniziali)\b"
    r"|\bscrivi\s+(?:esattamente|testualmente|letteralmente)\b"
    r"|\bnella\s+(?:tua\s+)?risposta(?:\s+finale)?\s+(?:scrivi|inserisci|riporta)\b"
    r"|\bin\s+(?:your|the)\s+(?:final\s+)?(?:answer|response|reply)\s+(?:write|say|print|output)\b"
    r").*$"
)

#: Set to "0" to measure the model alone (`nexgen local eval --bare`): the evaluation exists to find out
#: whether a model can be trusted, and a layer that hides the attack hides that too.
INJECTION_LAYER_ENV = "NEXGEN_LANE_INJECTION_LAYER"

#: What the model sees instead. Visible on purpose: an answer about the note can say a line was withheld.
NEUTRALIZED_LINE = "[riga neutralizzata dal motore: istruzione rivolta all'assistente]"


# Retrieved content is data, never an order: remove hidden comments and control characters, and neutralize
# lines that address the assistant with an instruction to override the request. A layer, not a guarantee:
# a rephrased injection can pass it, which is why the trap suite measures the model as well.
def sanitize_content(text: str) -> str:
    cleaned = _HTML_COMMENT_RE.sub("", str(text))
    cleaned = _INVISIBLE_RE.sub("", cleaned)
    if os.environ.get(INJECTION_LAYER_ENV, "1") == "0":
        return cleaned
    cleaned = _ADDRESSED_OVERRIDE_RE.sub(NEUTRALIZED_LINE, cleaned)
    return _CLASSIC_OVERRIDE_RE.sub(NEUTRALIZED_LINE, cleaned)


def terms(text: str) -> list[str]:
    words = re.findall(r"[A-Za-zÀ-ÿ0-9][\w.À-ÿ'-]{2,}", str(text))
    return [w for w in words if w.casefold() not in STOPWORDS]


def longest_term(items: Iterable[str]) -> str:
    values = [i for i in items if i]
    return max(values, key=len) if values else ""


def empty_result(result: str) -> bool:
    """Legacy text protocol. Internal drivers use ToolResult.usable instead."""
    return result.startswith("(")


def existing_file(cfg: LaneConfig, raw: str) -> tuple[str, str, str] | None:
    """Return (kind, relative path, owning root) only if the file is inside an allowed root.

    The owning root is part of the destination: with two repo roots that both
    contain ``nota.md``, an explicit ``/B/nota.md`` resolves to root B, and
    callers must keep that root until the read and the receipt. A bare
    relative path keeps first-root-wins order; only an explicit path binds.
    """
    cleaned = str(raw or "").strip().strip("`'\"")
    if not cleaned:
        return None
    roots: list[tuple[str, Path]] = [("vault", cfg.vault_root)] + [("repo", root) for root in cfg.repo_roots]
    candidates: list[Path] = [Path(cleaned)] if Path(cleaned).is_absolute() else [root / cleaned for _, root in roots]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if any(part in cfg.excluded_parts for part in resolved.parts):
            continue
        for kind, root in roots:
            try:
                root_resolved = root.resolve()
                rel = resolved.relative_to(root_resolved)
            except (OSError, ValueError):
                continue
            if resolved.is_file():
                # as_posix: su Windows rel avrebbe i backslash e i menu
                # smetterebbero di corrispondere alle decisioni con gli slash.
                if resolved.suffix.casefold() == ".pdf":
                    return "pdf", rel.as_posix(), str(root_resolved)
                return kind, rel.as_posix(), str(root_resolved)
            break  # this candidate belongs to this root but is not a file: try the next root
    return None


def pinned_path(root: str, rel: str) -> str:
    """Canonical destination for a read: absolute when the owning root is known."""
    if root:
        return str(Path(root) / rel)
    return rel

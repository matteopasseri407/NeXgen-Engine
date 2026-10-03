"""Source selection and content normalization, independent of orchestration."""

from __future__ import annotations

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
        "una",
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


# Retrieved content is data: remove hidden comments and control characters.
def sanitize_content(text: str) -> str:
    cleaned = _HTML_COMMENT_RE.sub("", str(text))
    return _INVISIBLE_RE.sub("", cleaned)


def terms(text: str) -> list[str]:
    words = re.findall(r"[A-Za-zÀ-ÿ0-9][\w.À-ÿ'-]{2,}", str(text))
    return [w for w in words if w.casefold() not in STOPWORDS]


def longest_term(items: Iterable[str]) -> str:
    values = [i for i in items if i]
    return max(values, key=len) if values else ""


def empty_result(result: str) -> bool:
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

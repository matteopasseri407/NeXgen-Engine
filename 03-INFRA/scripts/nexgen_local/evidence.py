"""Verify answers against successful tool receipts and retrieved content."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .source_selection import PATH_RE
from .tools import refusal_kind

CLAIM_PHRASES = (
    "ho cercato",
    "ho ricercato",
    "ho eseguito",
    "ho effettuato",
    "ho lanciato",
    "ho avviato",
    "ho letto",
    "ho consultato",
    "ho aperto",
    "ho trovato il file",
    "ho trovato",
    "ho recuperato",
    "ho usato",
)

#: Phrases that claim a write. The lane has no write tool, so these are false
#: whatever the receipts say. Mail sending lives behind the compose gate, so
#: prose claims of sent mail are false too: only the gate's receipts count.
WRITE_CLAIM_PHRASES = (
    "ho salvato",
    "ho scritto",
    "ho creato",
    "ho aggiunto",
    "ho modificato",
    "ho eliminato",
    "ho cancellato",
    "ho applicato",
    "ho inviato",
    "ho mandato",
    "ho spedito",
)

#: Typed families: each claimed state must be produced by the engine.
_READ_CLAIM_PHRASES = (
    "ho letto",
    "ho consultato",
    "ho aperto",
    "ho trovato il file",
    "ho trovato",
    "ho recuperato",
)
_SEARCH_CLAIM_PHRASES = (
    "ho cercato",
    "ho ricercato",
)
_EXEC_CLAIM_PHRASES = (
    "ho eseguito",
    "ho effettuato",
    "ho lanciato",
    "ho avviato",
    "ho usato",
)
_VERIFY_CLAIM_PHRASES = (
    "ho verificato",
    "ho testato",
    "ho controllato i test",
    "ho superato i test",
)

#: Words that turn a generic "ho eseguito" into a test/verification claim.
#: The lane runs no tests, so these are always confabulations.
_TEST_WORDS = ("test", "verific", "collaud", "pytest", "coverage")

#: Passive/result claims without "ho": "file aggiornato", "test superati".
#: The lane can neither write nor run tests, so an unnegated match is always
#: a problem.
_WRITE_RESULT_RE = re.compile(
    r"\b(aggiornat[oaie]|modificat[oaie]|salvat[oaie]|scritt[oaie]|creat[oaie]"
    r"|aggiunt[oaie]|eliminat[oaie]|cancellat[oaie]|applicat[oaie])\b",
    re.I,
)
_TEST_RESULT_RE = re.compile(
    r"\btest\s+(superat\w*|passat\w*|eseguit\w*|ok|verdi)\b"
    r"|\bverific\w*\s+(superat\w*|completat\w*|ok)\b"
    r"|\bsuperat\w*\s+(i\s+)?test\b"
    r"|\bverificat[oaie]\b",
    re.I,
)

#: Absence declarations: the honest outcome when retrieval produced nothing.
#: This is intentionally strict: a bare "non" ("non supera 900 euro") is a
#: negated fact about the world, not a declaration that the engine found
#: nothing. Only phrases that state the lack of information count.
_ABSENCE_RE = re.compile(
    r"\b(non ho trovato|non trovo|non ho recuperato|non ho accesso|non ho informazioni"
    r"|non esiste|non esistono|non risulta|non risultano"
    r"|non disponibile|non disponibili|non presente|non accessibile|non recuperabile"
    r"|non so"
    r"|nessun\w* (risultato|risultati|nota|note|file|documento|documenti|contenuto|contenuti|informazione|informazioni)"
    r"|non ha prodotto risultati)\b",
    re.I,
)


def declares_absence(text: str) -> bool:
    """True when the answer states that no information was found.

    ``Il budget non supera 900 euro`` asserts a fact (and must be grounded);
    ``Non ho trovato nessuna nota`` declares the absence of information.
    """
    return bool(_ABSENCE_RE.search(str(text or "")))


_DIGIT_RE = re.compile(r"\d")


def absence_with_facts(text: str) -> bool:
    """True when an absence declaration carries extra asserted facts.

    ``Non ho trovato la nota. Il budget e' 900 euro`` opens with honesty and
    closes with invention: digits outside cited paths are specific facts the
    empty retrieval cannot support. A bare ``Non ho trovato nessuna nota su
    Falco Rosso`` (or a reference such as ``01-NOTE/x.md``) has none.
    """
    content = str(text or "")
    if not declares_absence(content):
        return False
    stripped = _ABSENCE_RE.sub(" ", content)
    stripped = PATH_RE.sub(" ", stripped)
    return bool(_DIGIT_RE.search(stripped))


#: The engine's own outcome when a requested retrieval produced nothing. Used
#: directly by the drivers instead of asking the model to report the void:
#: operational outcomes are built from receipts, never from prose that merely
#: dodged the filters.
ENGINE_ABSENCE = "Non ho trovato informazioni: la ricerca non ha prodotto risultati."

#: The engine's own outcome when retrieval itself failed. Deliberately unlike
#: ENGINE_ABSENCE: "no mail found" must never be reported when the account is
#: disconnected, the backend is down, or a path was refused.
ENGINE_ERROR = "Non ho potuto completare la ricerca: backend non raggiungibile o accesso non disponibile."


def honest_empty_outcome(text: str) -> bool:
    """True for an engine-grade empty outcome and nothing else.

    A genuine absence declaration without smuggled facts, or the engine's own
    error sentence. Used by the claim check and the benchmark alike.
    """
    content = str(text or "").strip()
    if content == ENGINE_ERROR:
        return True
    return declares_absence(content) and not absence_with_facts(content)


def _no_content_retrieved(calls: list[Any], collected: str) -> bool:
    """True when retrieval ran but left nothing the answer could use.

    ``calls`` are ToolCall objects (name/args/ok); ``collected`` is the
    engine's assembled text. A ``source == "none"`` task makes no calls and
    never qualifies: the model answers directly there.
    """
    if collected or not calls:
        return False
    paths: list[str] = []
    web = False
    for call in calls:
        if not getattr(call, "ok", False):
            continue
        name = str(getattr(call, "name", "") or "")
        args = getattr(call, "args", {}) or {}
        if name in _READ_TOOLS and (args.get("path") or args.get("id")):
            paths.append(str(args.get("path") or f"{name.split('_')[1]}:{args['id']}"))
        if name == "web_search":
            web = True
    return not paths and not web


def retrieval_outcome(calls: list[Any], refusals: list[str], collected: str) -> str:
    """One of "ok", "empty", or "error" for a finished retrieval.

    "ok": there is content to work with, or nothing was requested.
    "empty": the backends answered and found nothing.
    "error": at least one refusal signals a backend-side failure
    (unreachable root, missing dependency, failed command, refused path).
    """
    if not _no_content_retrieved(calls, collected):
        return "ok"
    if any(refusal_kind(refusal) == "error" for refusal in refusals):
        return "error"
    return "empty"


def engine_sentence(outcome: str) -> str:
    """The engine's own words for a non-ok retrieval outcome."""
    return ENGINE_ERROR if outcome == "error" else ENGINE_ABSENCE


#: A negation right before a claim turns it into a true statement of absence
#: ("non ho letto la nota"): that is not a confabulation.
_NEGATION_RE = re.compile(r"\b(non|nessun|nessuna|nessuno|senza|niente|mai)\b", re.I)

#: Tools whose successful receipt is evidence that a source was actually read.
#: File reads carry ``path``; mail/drive/calendar reads carry ``id``
#: (engine-found, never model-invented).
_READ_TOOLS = ("read_vault", "read_repo", "read_pdf", "read_mail", "read_drive", "read_calendar", "read_outlook")


def _has_claim(low: str, phrases: Iterable[str]) -> bool:
    """True when a phrase occurs without a negation in the preceding context."""
    for phrase in phrases:
        start = 0
        while True:
            index = low.find(phrase, start)
            if index < 0:
                break
            if not _NEGATION_RE.search(low[max(0, index - 40) : index]):
                return True
            start = index + len(phrase)
    return False


def _evidence(receipts: list[dict[str, Any]]) -> tuple[list[str], bool, bool]:
    """(paths read, vault/web searched, web searched) from successful receipts only."""
    paths: list[str] = []
    searched = False
    web = False
    for receipt in receipts:
        if not receipt.get("ok"):
            continue
        name = str(receipt.get("tool") or "")
        args = receipt.get("args") or {}
        if name in ("search_vault", "web_search"):
            searched = True
        if name == "web_search":
            web = True
        if name in _READ_TOOLS and (args.get("path") or args.get("id")):
            paths.append(str(args.get("path") or f"{name.split('_')[1]}:{args['id']}"))
    return paths, searched, web


def _path_matches(cited: str, read: str) -> bool:
    # Separator-insensitive: le ricevute portano path assoluti del sistema
    # (backslash su Windows), le citazioni usano gli slash.
    left = cited.strip("./").replace("\\", "/").casefold()
    right = read.strip("./").replace("\\", "/").casefold()
    return left == right or right.endswith("/" + left) or left.endswith("/" + right)


def _cited_paths(text: str) -> list[str]:
    """Paths the answer presents as sources, ignoring negated mentions."""
    cited: list[str] = []
    for match in PATH_RE.finditer(text):
        if _NEGATION_RE.search(text[max(0, match.start() - 40) : match.start()]):
            continue
        path = match.group(1)
        if path not in cited:
            cited.append(path)
    return cited


def _unnegated_in(word: str, haystack_low: str) -> bool:
    """True when the word occurs in the haystack outside a negated span."""
    for match in re.finditer(r"\b" + re.escape(word) + r"\b", haystack_low):
        if not _NEGATION_RE.search(haystack_low[max(0, match.start() - 40) : match.start()]):
            return True
    return False


def _grounded_in_collected(match_start: int, match_end: int, text: str, collected: str | None) -> bool:
    """True when the matched claim restates retrieved content.

    The lane may summarise what the engine found ("il libro e' scritto in
    inglese" when the web result says so); it may not assert new states
    ("file aggiornato") and decorate them with a nearby citation. Grounding
    requires the matched word itself plus at least one neighbouring content
    word to appear in ``collected`` — and the matched word must occur there
    outside a negated span, otherwise ``Il file non e' stato aggiornato``
    would ground a ``File aggiornato`` claim with the opposite meaning.
    No collected context means no grounding.
    """
    if not collected:
        return False
    matched = re.findall(r"[a-zà-ÿ0-9]+", str(text[max(0, match_start) : match_end]).casefold())
    if not matched:
        return False
    collected_low = collected.casefold()
    if not all(_unnegated_in(word, collected_low) for word in matched):
        return False
    window = re.findall(r"[a-zà-ÿ0-9]+", str(text[max(0, match_start - 80) : match_end + 80]).casefold())
    window_tokens = {token for token in window if len(token) >= 4}
    collected_tokens = {token for token in re.findall(r"[a-zà-ÿ0-9]+", collected_low) if len(token) >= 4}
    return len((window_tokens & collected_tokens) - set(matched)) >= 1


def _cited_drive_name(cited: str, receipts: list[dict[str, Any]]) -> bool:
    """True when the cited filename is a Drive file the engine actually read.

    Drive ids are opaque, so the model rightly cites the human name
    (``[Contratto.txt]``); the receipt carries that name alongside the id.
    A cited name with no matching successful read is still a problem.
    """
    want = cited.split("/")[-1].casefold()
    for receipt in receipts:
        if receipt.get("tool") != "read_drive" or not receipt.get("ok"):
            continue
        name = str((receipt.get("args") or {}).get("name", ""))
        if name and name.split("/")[-1].casefold() == want:
            return True
    return False


def _has_result_pattern(
    low: str,
    pattern: re.Pattern[str],
    text: str = "",
    collected: str | None = None,
) -> bool:
    """True when the pattern claims an execution state the lane cannot produce.

    A match is exempt only when it restates retrieved content (see
    ``_grounded_in_collected``): proximity to a citation alone never suffices,
    otherwise ``File aggiornato ... Fonte: [nota.md]`` would launder a write
    the engine never performed.
    """
    for match in pattern.finditer(low):
        if _NEGATION_RE.search(low[max(0, match.start() - 40) : match.start()]):
            continue
        if _grounded_in_collected(match.start(), match.end(), text or low, collected):
            continue
        return True
    return False


def _mentions_test(low: str) -> bool:
    return any(word in low for word in _TEST_WORDS)


def verify_answer(answer: str, receipts: list[dict[str, Any]], collected: str | None = None) -> list[str]:
    """Machine check of the answer's claims, against successful receipts only.

    An empty list means every claim about work done and every cited source is
    backed by what the engine actually read. A failed search is not evidence;
    a path cited but never read is a problem; a claimed write or a claimed
    test is always one. Each family needs its own evidence: "ho letto" needs
    a read receipt, "ho cercato" a search receipt, and "aggiornato"/"test
    superati" fail even with receipts because the lane can neither write nor
    run tests.

    ``collected`` is the engine's retrieved text. When the caller passes it
    explicitly (``""`` means "retrieval ran but produced nothing") and the
    answer asserts facts without any absence phrasing, that is an invention
    even without claim words. ``None`` (the default for callers without
    retrieval context) skips that check.
    """
    text = str(answer or "")
    low = text.casefold()
    problems: list[str] = []
    successful = [receipt for receipt in receipts if receipt.get("ok")]
    paths, searched, web_ok = _evidence(receipts)
    if _has_claim(low, _READ_CLAIM_PHRASES) and not paths:
        problems.append("afferma una lettura senza una ricevuta di lettura riuscita")
    elif _has_claim(low, _READ_CLAIM_PHRASES) and not successful:
        problems.append("afferma lavoro svolto senza una ricevuta riuscita")
    if _has_claim(low, _SEARCH_CLAIM_PHRASES) and not searched:
        problems.append("afferma una ricerca senza una ricevuta di ricerca riuscita")
    if _has_claim(low, _EXEC_CLAIM_PHRASES):
        if _mentions_test(low):
            problems.append("afferma test o verifiche che la lane non puo' eseguire")
        elif not successful:
            problems.append("afferma lavoro svolto senza una ricevuta riuscita")
    if _has_claim(low, _VERIFY_CLAIM_PHRASES):
        problems.append("afferma test o verifiche che la lane non puo' eseguire")
    if _has_claim(low, WRITE_CLAIM_PHRASES):
        problems.append("afferma una scrittura che la lane non puo' eseguire")
    if _has_result_pattern(low, _WRITE_RESULT_RE, text, collected):
        problems.append("afferma una scrittura che la lane non puo' eseguire")
    if _has_result_pattern(low, _TEST_RESULT_RE, text, collected):
        problems.append("afferma test o verifiche che la lane non puo' eseguire")
    for cited in _cited_paths(text):
        if any(_path_matches(cited, read) for read in paths):
            continue
        if web_ok and collected and cited.casefold() in collected.casefold():
            continue
        if _cited_drive_name(cited, receipts):
            continue
        problems.append(f"cita una fonte non letta: {cited}")
    if collected is not None and not collected and receipts and not paths and not web_ok:
        if low.strip() and not honest_empty_outcome(text):
            problems.append("afferma fatti senza contenuto recuperato dal motore")
    # Deduplicate while keeping order: one family, one reason.
    seen: set[str] = set()
    unique: list[str] = []
    for problem in problems:
        if problem not in seen:
            seen.add(problem)
            unique.append(problem)
    return unique

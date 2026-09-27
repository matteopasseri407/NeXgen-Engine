"""Read-only tools with canonical confinement, caps and fail-closed audit.

Every tool in this module can only read. There is no write tool, no shell
tool, no generic fetch: if a capability is not mounted here, a model cannot
hallucinate it into existence. Paths are resolved and confined to the
declared roots before anything is opened; reads are capped; every call is
written to a JSONL audit line, and an audit that cannot be written refuses
the call instead of proceeding without a receipt.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import LaneConfig

#: Keywords shorter than this are noise for vault search.
MIN_TERM = 2

#: Files larger than this are skipped by search instead of read whole.
MAX_SEARCH_BYTES = 1_000_000

#: Refusal texts that mean "the backend worked, there is just nothing here".
#: Everything else in parentheses is a failure: unreachable root, missing
#: dependency, failed command, refused path. For personal sources this
#: distinction is load-bearing: "no mail found" and "account disconnected"
#: must never share an outcome.
_EMPTY_REFUSALS = ("nessun risultato", "nessun testo estraibile")


def refusal_kind(text: str) -> str:
    """Classify a tool output: "ok", "empty", or "error".

    Success will never depend on printed text elsewhere (see ``RunResult``);
    this classifies the recorded refusal strings so the engine can tell an
    empty search from a broken backend.
    """
    low = str(text or "").strip().casefold()
    if not low.startswith("("):
        return "ok"
    if any(marker in low for marker in _EMPTY_REFUSALS):
        return "empty"
    return "error"


class ToolError(RuntimeError):
    """The call was refused (confinement, audit, missing dependency)."""


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    ok: bool
    chars: int


@dataclass
class RunResult:
    """Structured outcome of a subprocess call: exit code, stdout, stderr.

    Success is the exit code, never the printed text: a backend that exits 2
    while printing ``ERROR: ...`` on stdout is a failure, full stop.
    """

    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


def audit_event(cfg: LaneConfig, name: str, args: dict[str, Any], ok: bool, chars: int) -> None:
    """Append one JSONL receipt. Raises when the audit cannot be written."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tool": name,
        "args": args,
        "ok": ok,
        "chars": chars,
    }
    try:
        cfg.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with cfg.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise ToolError(f"audit non scrivibile, chiamata rifiutata: {exc}") from exc


class ToolRegistry:
    """The lane's whole capability surface: fourteen read-only tools."""

    def __init__(self, cfg: LaneConfig) -> None:
        self.cfg = cfg
        self.calls: list[ToolCall] = []
        #: Every "(...)" output recorded, in order: the engine needs the texts
        #: to tell "backend worked, nothing found" from "backend failed".
        #: Cleared together with ``calls`` before each run.
        self.refusals: list[str] = []

    # ------------------------------------------------------------------ audit

    def _audit(self, name: str, args: dict[str, Any], ok: bool, chars: int) -> None:
        audit_event(self.cfg, name, args, ok, chars)

    def _record(self, name: str, args: dict[str, Any], output: str) -> str:
        self.calls.append(ToolCall(name=name, args=args, ok=not output.startswith("("), chars=len(output)))
        if output.startswith("("):
            self.refusals.append(output)
        self._audit(name, args, ok=not output.startswith("("), chars=len(output))
        return output

    def _refuse(self, name: str, args: dict[str, Any], message: str) -> str:
        """A refusal is an event too: it leaves a receipt, or it never happened."""
        self.calls.append(ToolCall(name=name, args=args, ok=False, chars=0))
        self.refusals.append(message)
        self._audit(name, args, ok=False, chars=0)
        return message

    # ----------------------------------------------------------- confinement

    def _resolve(self, rel: str, roots: tuple[Path, ...]) -> Path | None:
        raw = str(rel or "").strip().strip("`'\"")
        if not raw:
            return None
        candidates: list[Path] = []
        path = Path(raw)
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.extend(root / raw for root in roots)
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if any(part in self.cfg.excluded_parts for part in resolved.parts):
                continue
            for root in roots:
                try:
                    resolved.relative_to(root.resolve())
                except (OSError, ValueError):
                    continue
                return resolved if resolved.is_file() else None
        return None

    def _read_text(self, path: Path) -> str:
        try:
            data = path.read_bytes()
        except OSError as exc:
            return f"(lettura fallita: {exc})"
        if b"\x00" in data[:4096]:
            return "(file binario, non leggibile come testo)"
        text = data.decode("utf-8", errors="replace")
        if len(text) > self.cfg.read_chars:
            text = text[: self.cfg.read_chars] + "\n[...troncato]"
        return text

    def _cap(self, text: str) -> str:
        if len(text) > self.cfg.read_chars:
            text = text[: self.cfg.read_chars] + "\n[...troncato]"
        return text

    # --------------------------------------------------------------- tools

    def search_vault(self, query: str, *, require_all: bool = False) -> str:
        terms = [t for t in re.split(r"[^0-9A-Za-zÀ-ÿ]+", str(query)) if len(t) >= MIN_TERM]
        if not terms:
            return self._refuse("search_vault", {"query": query}, "(query vuota)")
        root = self.cfg.vault_root
        if not root.is_dir():
            return self._refuse("search_vault", {"query": query}, "(vault non raggiungibile)")
        low_terms = [t.casefold() for t in terms]
        scored: list[tuple[int, int, str]] = []
        root_resolved = root.resolve()
        for path in root.rglob("*.md"):
            if any(part in self.cfg.excluded_parts for part in path.parts):
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            # Same destination check as direct reads: a symlink pointing
            # outside the vault must not leak its target's content.
            if any(part in self.cfg.excluded_parts for part in resolved.parts):
                continue
            try:
                resolved.relative_to(root_resolved)
            except (OSError, ValueError):
                continue
            try:
                if resolved.stat().st_size > MAX_SEARCH_BYTES:
                    continue
                text = resolved.read_text(errors="replace").casefold()
            except OSError:
                continue
            # The action loop searches with require_all: a generic word must
            # not drag in a note that does not match the distinctive terms.
            if require_all and not all(term in text for term in low_terms):
                continue
            rel = str(path.relative_to(root))
            low_rel = rel.casefold()
            count = sum(text.count(term) for term in low_terms)
            if not count:
                continue
            bonus = 1 if any(term in low_rel for term in low_terms) else 0
            scored.append((bonus, count, rel))
        scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        if not scored:
            return self._record("search_vault", {"query": query}, "(nessun risultato)")
        return self._record(
            "search_vault", {"query": query}, "\n".join(rel for _, _, rel in scored[: self.cfg.max_results])
        )

    def read_vault(self, path: str) -> str:
        target = self._resolve(path, (self.cfg.vault_root,))
        if target is None:
            return self._refuse("read_vault", {"path": path}, "(rifiutato: percorso fuori perimetro o inesistente)")
        return self._record("read_vault", {"path": path}, self._read_text(target))

    def read_repo(self, path: str) -> str:
        target = self._resolve(path, self.cfg.repo_roots)
        if target is None:
            return self._refuse("read_repo", {"path": path}, "(rifiutato: percorso fuori perimetro o inesistente)")
        return self._record("read_repo", {"path": path}, self._read_text(target))

    def read_pdf(self, path: str) -> str:
        target = self._resolve(path, (self.cfg.vault_root, *self.cfg.repo_roots))
        if target is None:
            return self._refuse("read_pdf", {"path": path}, "(rifiutato: percorso fuori perimetro o inesistente)")
        if target.suffix.lower() != ".pdf":
            return self._refuse("read_pdf", {"path": path}, "(non e' un PDF)")
        if not shutil.which(self.cfg.pdftotext_cmd):
            return self._refuse("read_pdf", {"path": path}, "(pdftotext non disponibile)")
        run = self._run([self.cfg.pdftotext_cmd, "-layout", str(target), "-"], timeout=60)
        if not run.ok:
            detail = (run.err or run.out).strip()[:200] or f"rc={run.rc}"
            return self._refuse("read_pdf", {"path": path}, f"(pdftotext fallito: {detail})")
        output = run.out.strip()
        if not output:
            return self._refuse("read_pdf", {"path": path}, "(nessun testo estraibile)")
        if len(output) > self.cfg.read_chars:
            output = output[: self.cfg.read_chars] + "\n[...troncato]"
        return self._record("read_pdf", {"path": path}, output)

    def web_search(self, query: str) -> str:
        query = str(query).strip()
        if not query:
            return self._refuse("web_search", {"query": query}, "(query vuota)")
        if not shutil.which(self.cfg.firecrawl_cmd):
            return self._refuse("web_search", {"query": query}, "(firecrawl-local non disponibile)")
        run = self._run([self.cfg.firecrawl_cmd, "search", query], timeout=120)
        if not run.ok:
            detail = (run.err or run.out).strip()[:200] or f"rc={run.rc}"
            return self._refuse("web_search", {"query": query}, f"(ricerca web fallita: {detail})")
        output = run.out.strip()
        if not output:
            return self._record("web_search", {"query": query}, "(nessun risultato)")
        if len(output) > self.cfg.read_chars:
            output = output[: self.cfg.read_chars] + "\n[...troncato]"
        return self._record("web_search", {"query": query}, output)

    # ------------------------------------------------- personal connectors

    def _read_drive_pdf(self, file_id: str) -> str | None:
        """Drive-hosted PDF through pdftotext: download, convert, forget.

        Returns the text (recorded by the caller) or None when pdftotext is
        missing or the bytes convert to nothing: then the original refusal
        stands. Temp file removed in every case.
        """
        import tempfile

        from .connectors import drive as drive_conn

        try:
            raw = drive_conn.download_bytes(file_id)
        except Exception:  # noqa: BLE001 - download failure keeps the original refusal
            return None
        if not shutil.which(self.cfg.pdftotext_cmd):
            return None
        tmp: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("wb", suffix=".pdf", delete=False) as handle:
                handle.write(raw)
                tmp = Path(handle.name)
            run = self._run([self.cfg.pdftotext_cmd, "-layout", str(tmp), "-"], timeout=60)
            if not run.ok:
                return None
            output = run.out.strip()
            if not output:
                return None
            lines = [
                f"[drive:{file_id} (pdf)]",
                "",
                output,
            ]
            return self._record("read_drive", {"id": file_id}, self._cap("\n".join(lines)))
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    def search_mail(self, query: str) -> str:
        """Gmail ids matching the query, one per line with sender/subject/date.

        Ids come only from here: the model picks from these lines, it never
        invents one. Unconfigured account is an error refusal, never an
        empty result: "no mail" must not hide "account disconnected".
        """
        from .connectors import ConnectorError
        from .connectors import gmail as gmail_conn

        query = str(query).strip()
        if not query:
            return self._refuse("search_mail", {"query": query}, "(query vuota)")
        try:
            hits = gmail_conn.search_messages(query, self.cfg.max_results)
        except ConnectorError as exc:
            return self._refuse("search_mail", {"query": query}, exc.refusal)
        if not hits:
            return self._record("search_mail", {"query": query}, "(nessun risultato)")
        lines: list[str] = []
        for hit in hits[: self.cfg.max_results]:
            try:
                meta = gmail_conn.get_message(hit["id"])
                lines.append(
                    f"{hit['id']} | {meta.get('date', '')} | {meta.get('from', '')} | {meta.get('subject', '')}"
                )
            except ConnectorError:
                lines.append(f"{hit['id']} |  |  | ")
        return self._record("search_mail", {"query": query}, "\n".join(lines))

    def read_mail(self, mid: str) -> str:
        from .connectors import ConnectorError
        from .connectors import gmail as gmail_conn

        mid = str(mid or "").strip()
        if not mid:
            return self._refuse("read_mail", {"id": mid}, "(id messaggio vuoto)")
        try:
            msg = gmail_conn.get_message(mid)
        except ConnectorError as exc:
            return self._refuse("read_mail", {"id": mid}, exc.refusal)
        lines = [
            f"[mail:{msg['id']}]",
            f"Da: {msg.get('from', '')}",
            f"A: {msg.get('to', '')}",
            f"Data: {msg.get('date', '')}",
            f"Oggetto: {msg.get('subject', '')}",
            f"Lettura: {msg.get('coverage', 'text')}",
            "",
            msg.get("body", ""),
        ]
        if msg.get("coverage") == "html":
            lines.append("\n(nota: testo estratto dall'HTML, formattazione rimossa)")
        elif msg.get("coverage") == "snippet":
            lines.append("\n(nota: solo anteprima disponibile, corpo non estraibile)")
        if msg.get("attachments"):
            lines.append("\nAllegati (nomi, non letti): " + ", ".join(msg["attachments"]))
        return self._record("read_mail", {"id": mid}, self._cap("\n".join(lines)))

    def search_drive(self, query: str) -> str:
        """Drive files matching the query, one per line with name/type/date."""
        from .connectors import ConnectorError
        from .connectors import drive as drive_conn

        query = str(query).strip()
        if not query:
            return self._refuse("search_drive", {"query": query}, "(query vuota)")
        try:
            hits = drive_conn.search_files(query, self.cfg.max_results)
        except ConnectorError as exc:
            return self._refuse("search_drive", {"query": query}, exc.refusal)
        if not hits:
            return self._record("search_drive", {"query": query}, "(nessun risultato)")
        lines = [
            f"{hit['id']} | {hit.get('name', '')} | {hit.get('mimeType', '')} | {hit.get('modifiedTime', '')}"
            for hit in hits[: self.cfg.max_results]
        ]
        return self._record("search_drive", {"query": query}, "\n".join(lines))

    def read_drive(self, file_id: str) -> str:
        from .connectors import ConnectorError
        from .connectors import drive as drive_conn

        file_id = str(file_id or "").strip()
        if not file_id:
            return self._refuse("read_drive", {"id": file_id}, "(id file vuoto)")
        try:
            doc = drive_conn.read_file(file_id)
        except ConnectorError as exc:
            if "non leggibile come testo" in exc.refusal and file_id:
                text = self._read_drive_pdf(file_id)
                if text is not None:
                    return text
            return self._refuse("read_drive", {"id": file_id}, exc.refusal)
        lines = [
            f"[drive:{doc.get('name', file_id)} ({doc['id']})]",
            f"Tipo: {doc.get('mimeType', '')}",
            f"Modificato: {doc.get('modifiedTime', '')}",
            "",
            str(doc.get("text", "")),
        ]
        return self._record(
            "read_drive", {"id": file_id, "name": doc.get("name", "")}, self._cap("\n".join(lines))
        )

    #: ISO dates (2026-10-01, optionally with a time) named in the query:
    #: they drive the search window instead of the default.
    _ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?\b")
    #: Bare day/month hints without a year are ignored on purpose: without a
    #: year the window would be a guess, and a guessed window hides events.
    _RELATIVE_DAY_RE = re.compile(r"\b(oggi|domani|dopodomani)\b", re.I)

    #: Default look-ahead when the query names no date: wide enough that a
    #: dentist three weeks out is still found, narrow enough to stay relevant.
    DEFAULT_WINDOW_DAYS = 30
    #: How many events the server may return before the lane filters and caps:
    #: searching first, limiting after — never the reverse.
    SEARCH_PAGE = 50

    def _calendar_window(self, query: str, now) -> tuple[str, str, str]:
        """(time_min, time_max, free text) from the query.

        ISO dates set the window to the named days (whole days, UTC);
        oggi/domani/dopodomani pin it relative to now; otherwise the
        default look-ahead applies. Date tokens are removed from the free
        text so they don't pollute the keyword filter.

        ``now`` must carry the USER's zone, not UTC: at 00:30 in Rome,
        "oggi" is the 28th local, not the 27th UTC. Callers pass
        ``datetime.now().astimezone()`` (system locale), never UTC.
        """
        from datetime import timedelta

        dates = sorted({match.group(0)[:10] for match in self._ISO_DATE_RE.finditer(query)})
        free = self._ISO_DATE_RE.sub(" ", query)
        if dates:
            time_min = f"{dates[0]}T00:00:00Z"
            time_max = f"{dates[-1]}T23:59:59Z"
            return time_min, time_max, free
        if self._RELATIVE_DAY_RE.search(query):
            found = {match.group(1).casefold() for match in self._RELATIVE_DAY_RE.finditer(query)}
            offset = 2 if "dopodomani" in found else (1 if "domani" in found else 0)
            start = (now + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            free = self._RELATIVE_DAY_RE.sub(" ", free)
            return start.isoformat(), end.isoformat(), free
        return now.isoformat(), (now + timedelta(days=self.DEFAULT_WINDOW_DAYS)).isoformat(), free

    def search_calendar(self, query: str) -> str:
        """Upcoming events matching the query, one per line with time/title.

        Ids come only from here: read_calendar accepts an engine-found id.
        Dates named in the query set the window; the free text filters
        server-side first (``q``) over a wide page, then locally — the cap
        applies to matches, never to the raw listing.
        """
        from datetime import datetime

        from .connectors import ConnectorError
        from .connectors import calendar as calendar_conn

        query = str(query).strip()
        if not query:
            return self._refuse("search_calendar", {"query": query}, "(query vuota)")
        # System locale, never UTC: day boundaries are the user's, and the
        # ISO offsets travel to the provider untouched.
        now = datetime.now().astimezone()
        time_min, time_max, free = self._calendar_window(query, now)
        try:
            items = calendar_conn.list_events(
                "primary",
                time_min,
                time_max,
                self.SEARCH_PAGE,
                q=" ".join(free.split()),
            )
        except ConnectorError as exc:
            return self._refuse("search_calendar", {"query": query}, exc.refusal)
        words = [w.casefold() for w in free.split() if len(w) >= 2]
        matching = [
            item for item in items
            if not words or any(w in f"{item.get('summary', '')} {item.get('description', '')} {item.get('location', '')}".casefold() for w in words)
        ]
        if not matching:
            return self._record("search_calendar", {"query": query}, "(nessun risultato)")
        return self._record(
            "search_calendar", {"query": query},
            "\n".join(calendar_conn.describe_event(item) for item in matching[: self.cfg.max_results]),
        )

    def read_calendar(self, event_id: str) -> str:
        from .connectors import ConnectorError
        from .connectors import calendar as calendar_conn

        event_id = str(event_id or "").strip()
        if not event_id:
            return self._refuse("read_calendar", {"id": event_id}, "(id evento vuoto)")
        try:
            item = calendar_conn.get_event("primary", event_id)
        except ConnectorError as exc:
            return self._refuse("read_calendar", {"id": event_id}, exc.refusal)
        lines = [
            f"[calendar:{event_id}]",
            f"Titolo: {item.get('summary', '')}",
            f"Inizio: {calendar_conn.when(item.get('start'))}",
            f"Fine: {calendar_conn.when(item.get('end'))}",
            f"Luogo: {item.get('location', '')}",
            "",
            str(item.get("description", "")),
        ]
        return self._record("read_calendar", {"id": event_id}, self._cap("\n".join(lines)))

    def search_outlook(self, query: str) -> str:
        """Outlook ids matching the query, one per line with sender/subject/date.

        Same contract as Gmail: ids only from here, unconfigured account is
        an error refusal, never an empty result.
        """
        from .connectors import ConnectorError
        from .connectors import outlook as outlook_conn

        query = str(query).strip()
        if not query:
            return self._refuse("search_outlook", {"query": query}, "(query vuota)")
        try:
            hits = outlook_conn.search_messages(query, self.cfg.max_results)
        except ConnectorError as exc:
            return self._refuse("search_outlook", {"query": query}, exc.refusal)
        if not hits:
            return self._record("search_outlook", {"query": query}, "(nessun risultato)")
        lines: list[str] = []
        for hit in hits[: self.cfg.max_results]:
            try:
                meta = outlook_conn.get_message(hit["id"])
                lines.append(
                    f"{hit['id']} | {meta.get('date', '')} | {meta.get('from', '')} | {meta.get('subject', '')}"
                )
            except ConnectorError:
                lines.append(f"{hit['id']} |  |  | ")
        return self._record("search_outlook", {"query": query}, "\n".join(lines))

    def read_outlook(self, mid: str) -> str:
        from .connectors import ConnectorError
        from .connectors import outlook as outlook_conn

        mid = str(mid or "").strip()
        if not mid:
            return self._refuse("read_outlook", {"id": mid}, "(id messaggio vuoto)")
        try:
            msg = outlook_conn.get_message(mid)
        except ConnectorError as exc:
            return self._refuse("read_outlook", {"id": mid}, exc.refusal)
        lines = [
            f"[outlook:{msg['id']}]",
            f"Da: {msg.get('from', '')}",
            f"A: {msg.get('to', '')}",
            f"Data: {msg.get('date', '')}",
            f"Oggetto: {msg.get('subject', '')}",
            f"Lettura: {msg.get('coverage', 'text')}",
            "",
            msg.get("body", ""),
        ]
        if msg.get("coverage") == "html":
            lines.append("\n(nota: testo estratto dall'HTML, formattazione rimossa)")
        elif msg.get("coverage") == "snippet":
            lines.append("\n(nota: solo anteprima disponibile, corpo non estraibile)")
        if msg.get("attachments"):
            lines.append("\nAllegati (nomi, non letti): " + ", ".join(msg["attachments"]))
        return self._record("read_outlook", {"id": mid}, self._cap("\n".join(lines)))

    def engine_status(self) -> str:
        from .config import default_engine_root

        root = default_engine_root()
        lines = [f"engine_root: {root}"]
        version_file = root / "VERSION"
        lines.append(f"version: {version_file.read_text().strip() if version_file.is_file() else 'sconosciuta'}")
        if shutil.which("git") and (root / ".git").exists():
            run = self._run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], timeout=15)
            head = run.out.strip() if run.ok else ""
            lines.append(f"head: {head or 'sconosciuto'}")
        lines.append(f"vault_root: {self.cfg.vault_root} ({'ok' if self.cfg.vault_root.is_dir() else 'assente'})")
        return self._record("engine_status", {}, "\n".join(lines))

    # ------------------------------------------------------------- plumbing

    @staticmethod
    def _run(cmd: list[str], timeout: int) -> RunResult:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return RunResult(proc.returncode, proc.stdout, proc.stderr)
        except (OSError, subprocess.SubprocessError) as exc:
            return RunResult(1, "", str(exc))

    def call(self, name: str, args: dict[str, Any]) -> str:
        """Dispatch by tool name; unknown names are refused, never guessed."""
        table = {
            "search_vault": lambda a: self.search_vault(str(a.get("query", ""))),
            "read_vault": lambda a: self.read_vault(str(a.get("path", ""))),
            "read_repo": lambda a: self.read_repo(str(a.get("path", ""))),
            "read_pdf": lambda a: self.read_pdf(str(a.get("path", ""))),
            "web_search": lambda a: self.web_search(str(a.get("query", ""))),
            "search_mail": lambda a: self.search_mail(str(a.get("query", ""))),
            "read_mail": lambda a: self.read_mail(str(a.get("id", ""))),
            "search_drive": lambda a: self.search_drive(str(a.get("query", ""))),
            "read_drive": lambda a: self.read_drive(str(a.get("id", ""))),
            "search_calendar": lambda a: self.search_calendar(str(a.get("query", ""))),
            "read_calendar": lambda a: self.read_calendar(str(a.get("id", ""))),
            "search_outlook": lambda a: self.search_outlook(str(a.get("query", ""))),
            "read_outlook": lambda a: self.read_outlook(str(a.get("id", ""))),
            "engine_status": lambda a: self.engine_status(),
        }
        if name not in table:
            return f"(strumento sconosciuto: {name})"
        return table[name](args)


def audit_writable(cfg: LaneConfig) -> bool:
    """Doctor helper: can the audit file be appended to right now."""
    try:
        cfg.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with cfg.audit_path.open("a", encoding="utf-8"):
            pass
        return True
    except OSError:
        return False

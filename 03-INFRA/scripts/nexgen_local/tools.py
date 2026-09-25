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


class ToolError(RuntimeError):
    """The call was refused (confinement, audit, missing dependency)."""


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    ok: bool
    chars: int


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
    """The lane's whole capability surface: six read-only tools."""

    def __init__(self, cfg: LaneConfig) -> None:
        self.cfg = cfg
        self.calls: list[ToolCall] = []

    # ------------------------------------------------------------------ audit

    def _audit(self, name: str, args: dict[str, Any], ok: bool, chars: int) -> None:
        audit_event(self.cfg, name, args, ok, chars)

    def _record(self, name: str, args: dict[str, Any], output: str) -> str:
        self.calls.append(ToolCall(name=name, args=args, ok=not output.startswith("("), chars=len(output)))
        self._audit(name, args, ok=not output.startswith("("), chars=len(output))
        return output

    def _refuse(self, name: str, args: dict[str, Any], message: str) -> str:
        """A refusal is an event too: it leaves a receipt, or it never happened."""
        self.calls.append(ToolCall(name=name, args=args, ok=False, chars=0))
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

    # --------------------------------------------------------------- tools

    def search_vault(self, query: str) -> str:
        terms = [t for t in re.split(r"[^0-9A-Za-zÀ-ÿ]+", str(query)) if len(t) >= MIN_TERM]
        if not terms:
            return self._refuse("search_vault", {"query": query}, "(query vuota)")
        root = self.cfg.vault_root
        if not root.is_dir():
            return self._refuse("search_vault", {"query": query}, "(vault non raggiungibile)")
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
            rel = str(path.relative_to(root))
            low_rel = rel.casefold()
            count = sum(text.count(t.casefold()) for t in terms)
            if not count:
                continue
            bonus = 1 if any(t.casefold() in low_rel for t in terms) else 0
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
        output = self._run([self.cfg.pdftotext_cmd, "-layout", str(target), "-"], timeout=60)
        output = output.strip()
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
        output = self._run([self.cfg.firecrawl_cmd, "search", query], timeout=120).strip()
        if not output:
            return self._record("web_search", {"query": query}, "(nessun risultato)")
        if len(output) > self.cfg.read_chars:
            output = output[: self.cfg.read_chars] + "\n[...troncato]"
        return self._record("web_search", {"query": query}, output)

    def engine_status(self) -> str:
        from .config import default_engine_root

        root = default_engine_root()
        lines = [f"engine_root: {root}"]
        version_file = root / "VERSION"
        lines.append(f"version: {version_file.read_text().strip() if version_file.is_file() else 'sconosciuta'}")
        if shutil.which("git") and (root / ".git").exists():
            head = self._run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], timeout=15).strip()
            lines.append(f"head: {head or 'sconosciuto'}")
        lines.append(f"vault_root: {self.cfg.vault_root} ({'ok' if self.cfg.vault_root.is_dir() else 'assente'})")
        return self._record("engine_status", {}, "\n".join(lines))

    # ------------------------------------------------------------- plumbing

    @staticmethod
    def _run(cmd: list[str], timeout: int) -> str:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return proc.stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    def call(self, name: str, args: dict[str, Any]) -> str:
        """Dispatch by tool name; unknown names are refused, never guessed."""
        table = {
            "search_vault": lambda a: self.search_vault(str(a.get("query", ""))),
            "read_vault": lambda a: self.read_vault(str(a.get("path", ""))),
            "read_repo": lambda a: self.read_repo(str(a.get("path", ""))),
            "read_pdf": lambda a: self.read_pdf(str(a.get("path", ""))),
            "web_search": lambda a: self.web_search(str(a.get("query", ""))),
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

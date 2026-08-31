#!/usr/bin/env python3
"""Council TUI — minimal inline live view for council orchestration.

Renders the council/relay state in the SAME terminal that launched the
council, inspired by Mission Control's fleet/task boards but without any
new dependency (stdlib only). When stdout is not a TTY (piped, CI, an
agent's tool capture) the TUI degrades to plain line logs so it never
breaks the non-interactive path.

Design:
  - No curses, no rich. ANSI VT100 only + shutil.get_terminal_size.
  - In-place redraw: clear previous frame and reprint, every 0.2s while a
    seat runs. Header / rows / footer fit in ~8-12 lines even for a 5-stage
    relay.
  - Driven by a tiny shared state dict that _run_mode / _run_relay_stage
    update; the render thread does not parse council internals.
  - Used behind --watch (opt-in) first, later default-on-TTY if preferred.

Preview: run `python3 tui.py --demo` or `python3 demo_tui.py`.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

Status = Literal["pending", "running", "done", "error", "quarantined"]

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class SeatRow:
    role: str
    name: str
    model: str
    cli: str = ""
    status: Status = "pending"
    elapsed: float = 0.0
    timeout: float = 300.0
    tokens: int | None = None
    cost: float | None = None
    verdict: str = "—"
    # for relay quarantine display
    quarantined_until: str = ""
    # internal: when this row started running (monotonic), for live elapsed
    _run_started_at: float | None = None


@dataclass
class CouncilState:
    mode: str  # brainstorm | challenge | code-review | relay
    label: str  # question / plan / diff name
    rows: list[SeatRow] = field(default_factory=list)
    total_tokens: int = 0
    total_cost: float = 0.0
    started_at: float = field(default_factory=time.monotonic)
    quarantine_note: str = ""


# ---------------------------------------------------------------------------
# Formatting helpers (no deps)
# ---------------------------------------------------------------------------

def _term_width(default: int = 88) -> int:
    try:
        return shutil.get_terminal_size(fallback=(default, 24)).columns
    except (OSError, ValueError):
        return default

def _fmt_elapsed(elapsed: float, timeout: float, status: Status) -> str:
    if status == "pending":
        return "—"
    if status == "running":
        return f"{elapsed:4.1f}s/{timeout:.0f}s"
    return f"{elapsed:4.1f}s"

def _fmt_tokens(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n:,}"

def _fmt_cost(c: float | None) -> str:
    if c is None:
        return "—"
    # Mission Control shows 4 decimals for small costs, 2 otherwise
    if c < 0.01:
        return f"${c:.4f}"
    return f"${c:.2f}"

def _status_icon(s: Status) -> str:
    return {
        "pending": "○",
        "running": "●",
        "done": "✔",
        "error": "✖",
        "quarantined": "◐",
    }.get(s, "·")

def _progress_pct(row: SeatRow) -> int:
    if row.status == "done":
        return 100
    if row.status in ("pending", "quarantined"):
        return 0
    if row.status == "error":
        return 0
    # running: elapsed/timeout capped 0-99 until done
    if row.timeout <= 0:
        return 0
    pct = int((row.elapsed / row.timeout) * 100)
    return max(0, min(99, pct))

def _progress_bar(pct: int, width: int = 6) -> str:
    filled = int((pct / 100) * width)
    return "█" * filled + "░" * (width - filled)

def _overall_pct(state: CouncilState) -> int:
    if not state.rows:
        return 0
    # relay: stages done / total weighted, single seat: its own pct
    if state.mode == "relay":
        done = sum(1 for r in state.rows if r.status == "done")
        # plus current running progress fractional
        running = next((r for r in state.rows if r.status == "running"), None)
        frac = (_progress_pct(running) / 100) if running else 0
        return int(((done + frac) / len(state.rows)) * 100)
    # single seat
    return _progress_pct(state.rows[0])

# ANSI — keep it minimal and reset-safe
_ANSI_HIDE = "\x1b[?25l"
_ANSI_SHOW = "\x1b[?25h"
_ANSI_CLEAR_LINE = "\x1b[2K"
_ANSI_RESET = "\x1b[0m"
_ANSI_DIM = "\x1b[2m"
_ANSI_BOLD = "\x1b[1m"
_ANSI_CYAN = "\x1b[36m"
_ANSI_YELLOW = "\x1b[33m"
_ANSI_GREEN = "\x1b[32m"
_ANSI_RED = "\x1b[31m"

def _supports_ansi() -> bool:
    # Respect NO_COLOR, dumb terminals, non-TTY
    if os.environ.get("NO_COLOR"):
        return False
    term = os.environ.get("TERM", "")
    if term == "dumb":
        return False
    # Windows 10+ needs VT processing enabled; os.system('') is the stdlib
    # trick that enables it without extra deps (no colorama). No-op on Unix.
    if os.name == "nt":
        try:
            os.system("")  # enable VT
        except OSError:
            pass
    return sys.stdout.isatty()

# ---------------------------------------------------------------------------
# Frame builder (pure function — testable)
# ---------------------------------------------------------------------------

def build_frame(state: CouncilState, width: int | None = None, use_color: bool | None = None) -> list[str]:
    """Return the lines that should be on screen for this state.

    The card is metadata-only: no model reasoning ever appears here.
    The calling model's reasoning stays in its own chat transcript,
    the council's model output stays in verdict.md — the TUI shows only
    progress, tokens and verdict.
    """
    w = width or _term_width()
    color = _supports_ansi() if use_color is None else use_color
    B = _ANSI_BOLD if color else ""
    D = _ANSI_DIM if color else ""
    R = _ANSI_RESET if color else ""
    Y = _ANSI_YELLOW if color else ""
    G = _ANSI_GREEN if color else ""

    elapsed_total = time.monotonic() - state.started_at
    mode_label = state.mode
    overall = _overall_pct(state)
    # header shows overall progress, lives — exactly w chars
    title = f" council • {mode_label} • {overall:3d}% • {state.label[:32]} "
    top_inner = title[: max(0, w - 4)]
    top = f"┌─{top_inner}─" + "─" * max(0, w - 4 - len(top_inner)) + "┐"
    if color:
        top = f"{D}{top}{R}"

    lines: list[str] = [top]

    # summary line: never contains reasoning
    hint = f" {len(state.rows)} seat(s) • {elapsed_total:4.1f}s • {overall}%"
    if state.total_tokens or state.total_cost:
        hint += f" • {_fmt_tokens(state.total_tokens)} tok • {_fmt_cost(state.total_cost)}"
    # FIX 2: live metrics — token/s
    if any(r.tokens for r in state.rows):
        # avg token/s across done+running
        total_tok = state.total_tokens or sum(r.tokens or 0 for r in state.rows)
        if elapsed_total > 0 and total_tok:
            hint += f" • {int(total_tok/elapsed_total)} tok/s"
    hint_line = f"│{D}{hint}{R}" if color else f"│{hint}"
    hint_line = hint_line + " " * max(0, w - len(_strip_ansi(hint_line)) - 1) + "│"
    lines.append(hint_line)
    # FIX 1: Consensus Matrix — compact verdicts per seat, no need to read full texts
    consensus = " │ ".join(
        f"{r.role[:6]}:{r.verdict if r.verdict!='—' else '…'}" if r.status!="pending" else f"{r.role[:6]}:○"
        for r in state.rows
    )
    consensus_line = f"│ Consensus: {consensus}"
    consensus_line = consensus_line + " " * max(0, w - len(_strip_ansi(consensus_line)) - 1) + "│"
    if color:
        # color verdicts
        consensus_line = consensus_line.replace("APPROVE", f"{G}APPROVE{R}").replace("REVISE", f"{Y}REVISE{R}").replace("REJECT", f"{_ANSI_RED}REJECT{R}")
    lines.append(consensus_line)
    lines.append(f"│{'─' * (w - 2)}│" if not color else f"│{D}{'─' * (w - 2)}{R}│")

    # column header — always shows progress %, never reasoning
    if state.mode == "relay":
        hdr = f"│ {'#':<2} {'role':<8} {'seat':<10} {'model':<14} {'progress':<11} {'status':<9} {'verdict':<7} │"
    else:
        hdr = f"│ {'seat':<12} {'model':<16} {'progress':<11} {'status':<9} {'elapsed':<9} {'cost':<7} │"
    if color:
        hdr = f"{B}{hdr}{R}"
    if len(_strip_ansi(hdr)) > w:
        # truncate stripped then re-add border
        hdr = _strip_ansi(hdr)[:w-1] + "│"
        if color:
            hdr = f"{B}{hdr}{R}"
    lines.append(hdr)
    lines.append(f"│{'─' * (w - 2)}│" if not color else f"│{D}{'─' * (w - 2)}{R}│")

    for idx, row in enumerate(state.rows, 1):
        icon = _status_icon(row.status)  # type: ignore[arg-type]
        pct = _progress_pct(row)
        bar = _progress_bar(pct)
        prog = f"{bar} {pct:3d}%"
        if color:
            if row.status == "running":
                icon = f"{Y}{icon}{R}"
                prog = f"{Y}{prog}{R}"
            elif row.status == "done" and row.verdict == "APPROVE":
                icon = f"{G}{icon}{R}"
            elif (row.status == "done" and row.verdict == "REJECT") or row.status == "error":
                icon = f"{_ANSI_RED}{icon}{R}"

        if state.mode == "relay":
            verdict = row.verdict if row.status in ("done", "error") else "—"
            line = (
                f"│ {idx:<2} {row.role[:8]:<8} {row.name[:10]:<10} {row.model[:14]:<14} "
                f"{prog:<11} {icon} {row.status:<7} {verdict:<7} │"
            )
        else:
            # single-seat modes: progress + status + elapsed + cost (no tokens col to keep width)
            line = (
                f"│ {row.name[:12]:<12} {row.model[:16]:<16} "
                f"{prog:<11} {icon} {row.status:<7} {_fmt_elapsed(row.elapsed, row.timeout, row.status):<9} {_fmt_cost(row.cost):<7} │"
            )
        if len(_strip_ansi(line)) > w:
            line = _strip_ansi(line)[: w - 2] + " │"
            if color:
                # re-apply minimal color on truncated line is not critical for test
                pass
        lines.append(line)

    if state.quarantine_note:
        q = f" ↻ {state.quarantine_note}"
        q_line = f"│{Y}{q}{R}" if color else f"│{q}"
        q_line = q_line + " " * max(0, w - len(_strip_ansi(q_line)) - 1) + "│"
        lines.append(q_line)

    # footer — card boundary, output below is separate
    bot = "└" + "─" * (w - 2) + "┘"
    if color:
        bot = f"{D}{bot}{R}"
    lines.append(bot)
    # FIX 3: keybinding hint — triage in <30s, not deep analysis (TTY only, chat uses compact)
    hint2 = " [a]pply winning | [f]ollow-up dissent | [q]abort — triage"
    if color:
        hint2 = f"{D}{hint2}{R}"
    if sys.stdout.isatty():
        lines.append(hint2[:w])
    return lines

def _strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", s)

# ---------------------------------------------------------------------------
# Live renderer (in-place redraw)
# ---------------------------------------------------------------------------

def _compact_live_line(state: CouncilState) -> str:
    """One-liner for non-TTY (OpenCode chat, piped logs): visible as streaming output."""
    overall = _overall_pct(state)
    elapsed = time.monotonic() - state.started_at
    running = next((r for r in state.rows if r.status == "running"), None)
    if running:
        # FIX 2: token/s live
        tok_s = ""
        if running.tokens and running.elapsed > 0:
            tok_s = f" {int(running.tokens/running.elapsed)} tok/s"
        detail = f"{running.name} {running.status} {running.elapsed:4.1f}s/{running.timeout:.0f}s{tok_s} {running.model}"
    else:
        done = sum(1 for r in state.rows if r.status == "done")
        detail = f"{done}/{len(state.rows)} done"
    # FIX 1: consensus in compact
    consensus = " ".join(r.verdict[0] if r.verdict not in ("—","…") else "·" for r in state.rows)
    base = f"[council][live] {overall:3d}% • {elapsed:4.1f}s • {detail} • [{consensus}]"
    if state.total_tokens:
        # FIX 2: avg tok/s
        avg = int(state.total_tokens/elapsed) if elapsed>0 else 0
        base += f" • {state.total_tokens} tok {avg} tok/s"
    if state.total_cost:
        base += f" • ${state.total_cost:.4f}"
    if state.quarantine_note:
        base += f" • {state.quarantine_note[:42]}"
    return base

class LiveRenderer:
    """In-place live renderer. Call tick() to redraw, close() to leave clean.

    P1: teardown is idempotent and ordered (cursor → alt screen → cooked → flush).
    P2: throttle 50ms with coalescing/dirty-flag, bounded queue for chat.
    """

    def __init__(self, state: CouncilState, interval: float = 0.05):
        self.state = state
        self.interval = interval
        self._prev_lines = 0
        self._is_tty = sys.stdout.isatty() and _supports_ansi()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._restored = False  # P1: idempotent guard
        self._dirty = True  # P2: coalescing
        # for non-TTY throttling — reduce splash on OpenCode
        self._last_compact = ""
        self._last_emit = 0.0
        self._last_pct = -1

    def _clear_prev(self) -> None:
        if self._prev_lines == 0 or not self._is_tty:
            return
        # move up and clear each line
        out = sys.stdout
        for _ in range(self._prev_lines):
            out.write("\x1b[1A")  # cursor up 1
            out.write(_ANSI_CLEAR_LINE + "\r")
        out.flush()

    def tick(self, force: bool = False) -> None:
        """P2: coalescing — render only if dirty or forced."""
        with self._lock:
            if force:
                self._dirty = True
            if not self._dirty and not force:
                return
            self._dirty = False
            lines = build_frame(self.state)
            if not self._is_tty:
                # non-TTY (OpenCode chat): compact only, throttled to 2s and 5% steps
                # to avoid splash — full card only at final stop
                now = time.monotonic()
                compact = _compact_live_line(self.state)
                pct = _overall_pct(self.state)
                pct_changed = abs(pct - self._last_pct) >= 5
                if self._prev_lines == 0:
                    print(compact, flush=True)
                    self._prev_lines = 1
                    self._last_compact = compact
                    self._last_emit = now
                    self._last_pct = pct
                    return
                if (compact != self._last_compact and pct_changed and (now - self._last_emit) >= 1.8):
                    print(compact, flush=True)
                    self._last_compact = compact
                    self._last_emit = now
                    self._last_pct = pct
                return
            self._clear_prev()
            for ln in lines:
                sys.stdout.write(_ANSI_CLEAR_LINE + ln + "\n")
            sys.stdout.flush()
            self._prev_lines = len(lines)

    def mark_dirty(self) -> None:
        """P2: mark that state changed and needs a render."""
        with self._lock:
            self._dirty = True

    def start(self) -> None:
        # hide cursor, draw first frame, start ticker
        if self._is_tty:
            sys.stdout.write(_ANSI_HIDE)
            sys.stdout.flush()
        self.tick(force=True)
        # P2: single loop for both TTY and chat, throttled via dirty
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            # P1: flag-only bump, P2: coalesce — mark dirty and let tick throttle
            now = time.monotonic()
            dirty = False
            for row in self.state.rows:
                if row.status == "running" and hasattr(row, "_run_started_at"):
                    started = getattr(row, "_run_started_at", None)
                    if started:
                        new_elapsed = now - started
                        if abs(new_elapsed - row.elapsed) > 0.05:
                            row.elapsed = new_elapsed
                            dirty = True
            if dirty:
                with self._lock:
                    self._dirty = True
                self.tick()

    def stop(self, final_draw: bool = True) -> None:
        # P1: idempotent, ordered teardown
        if self._restored:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        with self._lock:
            if self._restored:
                return
            self._restored = True
            if self._is_tty:
                self._clear_prev()
                if final_draw:
                    for ln in build_frame(self.state):
                        sys.stdout.write(ln + "\n")
                # P1 order: cursor → alt screen → cooked → flush
                sys.stdout.write(_ANSI_SHOW)
                # leave alternate screen if we ever used it (no-op if not)
                sys.stdout.write("\x1b[?1049l")
                sys.stdout.flush()
                # ensure childs are reaped — session.py already does, but
                # TUI also flushes to avoid fd leak
                try:
                    sys.stdout.flush()
                    sys.stderr.flush()
                except OSError:
                    pass
                self._prev_lines = 0
            else:
                if final_draw:
                    # P2: no full box splash on chat — compact final only
                    print(_compact_live_line(self.state) + " • final", flush=True)
                    # and a one-line verdict summary, not 8-line box
                    verdicts = " ".join(f"{r.role}:{r.verdict}" for r in self.state.rows if r.verdict not in ("—","…"))
                    if verdicts:
                        print(f"[council] final: {verdicts}", flush=True)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop(final_draw=True)

# ---------------------------------------------------------------------------
# Demo harness
# ---------------------------------------------------------------------------

def _demo_brainstorm() -> None:
    """Simulate a 1-seat brainstorm with token growth."""
    state = CouncilState(mode="brainstorm", label="Review open incidents — cost gate", rows=[
        SeatRow(role="L-Arch", name="scout", model="opus-4.5", cli="claude", status="running", timeout=300),
    ])
    with LiveRenderer(state, interval=0.15) as live:
        for i in range(28):
            time.sleep(0.2)
            row = state.rows[0]
            row.elapsed = i * 0.2 + 0.3
            # fake streaming tokens
            row.tokens = int(180 + i * 95 + (i % 3) * 40)
            row.cost = row.tokens * 0.000012
            live.tick(force=True)
        row.status = "done"
        row.verdict = "REVISE"
        row.elapsed = 6.8
        time.sleep(0.6)

def _demo_relay() -> None:
    """Simulate a 3-stage relay: done, running, pending + quarantine."""
    state = CouncilState(mode="relay", label="Ship cockpit TUI without rich dep", rows=[
        SeatRow(role="L-Arch", name="claude-opus", model="opus-4.5", cli="claude", status="done", elapsed=18.2, tokens=2140, cost=0.042, verdict="REVISE"),
        SeatRow(role="L-Code", name="codex", model="gpt-5", cli="codex", status="running", timeout=300, elapsed=0),
        SeatRow(role="L-Sys", name="opencode", model="kimi-k2", cli="opencode", status="pending", timeout=300),
    ])
    with LiveRenderer(state, interval=0.15) as live:
        # stage 2 runs
        for i in range(22):
            time.sleep(0.2)
            row = state.rows[1]
            row.elapsed = i * 0.2
            row.tokens = int(i * 110)
            row.cost = row.tokens * 0.00001
            state.total_tokens = 2140 + (row.tokens or 0)
            state.total_cost = 0.042 + (row.cost or 0)
            # inject quarantine mid-run
            if i == 12:
                state.quarantine_note = "pool openai:gpt-5 quarantined until 14:32Z — fallback to kimi"
            live.tick(force=True)
        state.rows[1].status = "done"
        state.rows[1].verdict = "APPROVE"
        state.rows[1].elapsed = 7.1
        state.rows[1].tokens = 1820
        state.rows[1].cost = 0.018
        state.quarantine_note = ""
        state.total_tokens = 3960
        state.total_cost = 0.060
        time.sleep(0.4)
        # stage 3 starts
        state.rows[2].status = "running"
        for i in range(14):
            time.sleep(0.2)
            row = state.rows[2]
            row.elapsed = i * 0.2
            row.tokens = int(i * 90)
            row.cost = row.tokens * 0.000008
            state.total_tokens = 3960 + (row.tokens or 0)
            state.total_cost = 0.060 + (row.cost or 0)
            live.tick(force=True)
        state.rows[2].status = "done"
        state.rows[2].verdict = "APPROVE"
        state.rows[2].elapsed = 4.4
        time.sleep(0.7)

def main_demo() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Council TUI demo")
    ap.add_argument("--mode", choices=["brainstorm", "relay", "both"], default="both")
    ap.add_argument("--plain", action="store_true", help="force non-TTY rendering")
    args = ap.parse_args()
    if args.plain:
        # monkey-patch isatty for demo
        sys.stdout.isatty = lambda: False  # type: ignore[method-assign]
    if args.mode in ("brainstorm", "both"):
        print("\n=== brainstorm (1 seat, live) ===\n")
        _demo_brainstorm()
        time.sleep(0.8)
    if args.mode in ("relay", "both"):
        print("\n=== relay (3 stages, live) ===\n")
        _demo_relay()
    print("\n[demos done] — this is stdlib ANSI only, no rich/textual.\n")

if __name__ == "__main__":
    main_demo()

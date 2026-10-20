"""What a reviewer reads: the package of one unit and the raw gate output.

`sup collect` judges a unit; this module turns that judgement and the facts
around it into the two files a supervisor opens: the package (verdict, gate
lines, skipped tests, requests, diffstat, a capped diff, the cost line) and the
raw file (every gate with its detail). It reads no git and runs nothing, so the
text can be tested and rebuilt on its own.
"""
from __future__ import annotations

import os

from . import supervision_requests as requests_module

#: The package carries at most this much diff; the cut is declared in the file.
DIFF_MAX_LINES = 400


def package_text(
    *, task_id: str, unit: str, branch: str, base: str, head: str, verdict: str,
    results: list, cost_line: str, diffstat: str, diff: str,
    skipped: list[str], request_scan: dict,
) -> str:
    """The file a supervisor reads: verdict, gate lines, skips, requests, diffstat, diff, cost."""
    kept, cut = _truncated(diff, DIFF_MAX_LINES)
    lines = [
        f"# Pacchetto di revisione: {unit}",
        "",
        f"- compito: {task_id}",
        f"- ramo: {branch}",
        f"- base: {base[:12]}",
        f"- head: {head[:12]}",
        f"- verdetto: {verdict}",
        f"- costo: {cost_line}",
        "",
        "## Cancelli",
        "",
        "| cancello | esito | motivo |",
        "|---|---|---|",
        *[f"| {result.name} | {'PASS' if result.ok else 'FAIL'} | {_cell(result.reason)} |" for result in results],
        "",
        *_skipped_section(skipped),
        *_request_section(request_scan),
        "## Diffstat",
        "",
        "```",
        diffstat,
        "```",
        "",
        "## Diff",
        "",
        "```diff",
        kept,
        "```",
        cut,
        "",
    ]
    return "\n".join(lines)


def raw_text(results: list, skipped: list[str]) -> str:
    """Every gate with its detail, for whoever has to read the failure in full."""
    blocks = []
    for result in results:
        header = f"{'PASS' if result.ok else 'FAIL'} {result.name}: {result.reason}"
        blocks.append(header + (f"\n\n{result.detail}" if result.detail else ""))
    return "\n\n".join(blocks) + "\n" + _skipped_raw(skipped)


def skipped_label() -> str:
    """What "not verified here" means for the platform the engine is running on."""
    if os.name == "nt":
        return "non verificato su Linux: lo deve dire la CI Linux"
    return "non verificato su questa piattaforma: lo devono dire le altre CI"


def _request_section(result: dict) -> list[str]:
    """The requests the executor's report makes, or why none could be read."""
    lines = ["## Richieste al proprietario", ""]
    if not result.get("report"):
        lines += [requests_module.NO_REPORT + ".", ""]
        return lines
    status = requests_module.section_status(result)
    if status:
        lines += [status + ".", ""]
        return lines
    if not result.get("hits"):
        lines += ["nessuna richiesta al proprietario.", ""]
        return lines
    lines += [f"- {hit['kind']} · «{hit['sentence']}»" for hit in result["hits"]]
    lines += ["", f"Lette in `{result['report']}`; una riga già scritta non si ripete.", ""]
    return lines


def _skipped_section(skipped: list[str]) -> list[str]:
    """The package section, empty-looking when nothing was skipped."""
    if not skipped:
        return ["## Test saltati su questa piattaforma", "", "nessun test saltato fra i file toccati.", ""]
    lines = ["## Test saltati su questa piattaforma", ""]
    lines += [f"- {entry}" for entry in skipped]
    lines += ["", skipped_label() + ".", ""]
    return lines


def _skipped_raw(skipped: list[str]) -> str:
    """The same list in the raw file, so a failure and its skips sit together."""
    if not skipped:
        return ""
    return "\n\nSKIPPED su questa piattaforma:\n" + "\n".join(f"- {entry}" for entry in skipped) + "\n"


def _truncated(text: str, limit: int) -> tuple[str, str]:
    """The diff up to `limit` lines, with the cut declared instead of silent."""
    lines = text.splitlines()
    if len(lines) <= limit:
        return text, ""
    return "\n".join(lines[:limit]), (
        f"\nDiff troncato: {len(lines) - limit} righe in più su {len(lines)} "
        f"(tetto dichiarato: {limit} righe). Il diff completo è in `git diff`."
    )


def _cell(text: str) -> str:
    """One table cell: the reason keeps one line and no pipe breaks the row."""
    return text.replace("|", "/").replace("\n", " ")[:300]

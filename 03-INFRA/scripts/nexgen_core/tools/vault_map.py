#!/usr/bin/env python3
"""Deterministic structural map of the vault (vault-map) for NeXgen Engine v2.

Ported into nexgen_core from vault-map.py (release): read-only, stdlib-only.

Read-only, stdlib-only. Reports three things about the wikilink graph:

  * broken links   -- [[targets]] that resolve to nothing (a renamed or
                      removed note leaves these behind);
  * orphan notes   -- notes with no structural inbound or outbound links;
  * hub notes      -- the most-linked notes (top inbound).

Resolution semantics deliberately mirror the vault-library MCP server
(exact relative path with or without extension, then unique stem, then
note title), so the map and the MCP never contradict each other. Targets
that exist on disk under an excluded tree (99-SECRETS) or as non-markdown
assets are "valid but excluded", never broken. Links coming FROM generated
index files (99-INDEX/note-index.md) are ignored for orphan and hub
purposes -- a generated index links everything, which would blind both
signals -- but still checked for brokenness (a dead link there means the
index is stale).

Modes: default human report, --json (machine-readable), --check (one
summary line plus the broken list; ALWAYS exit 0 -- this is a WARN-only
backstop for agent-doctor, never a gate).

Known limitation (parity with the MCP server's own extractor): links are
extracted verbatim, including inside code fences and inline code spans.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from collections import Counter
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.paths import resolve_vault_data

NOTE_EXTENSIONS = (".md", ".markdown", ".mdown", ".mdx")
FENCED_BLOCK_RE = re.compile(r"^ {0,3}(`{3,}|~{3,}).*?^ {0,3}\1`*\s*$", re.DOTALL | re.MULTILINE)
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
NON_WORD_RE = re.compile(r"[^a-z0-9]+")

SCAN_EXCLUDED_DIRS = {".git", ".obsidian", "node_modules", "__pycache__", ".venv", "venv", "99-SECRETS"}
# Markdown files that are infrastructure or exports, not knowledge notes:
# they are managed by other mechanisms (sync, generators) and must not
# pollute the orphan report.
NON_NOTE_PREFIXES = ("01-ME/cv-artifacts/", "03-INFRA/agent-universal-layer/")
# Generated indexes link everything; treating those links as structural
# would rescue every orphan and inflate every hub.
GENERATED_SOURCES = {"99-INDEX/note-index.md"}
ORPHAN_EXEMPT = {"00-START-HERE.md"} | GENERATED_SOURCES


def _normalize_lookup(value: str) -> str:
    lowered = value.lower().strip()
    for extension in NOTE_EXTENSIONS:
        lowered = lowered.removesuffix(extension)
    return NON_WORD_RE.sub(" ", lowered).strip()


def _extract_title(text: str, path: Path) -> str:
    content = FRONTMATTER_RE.sub("", text, count=1)
    heading = HEADING_RE.search(content)
    if heading:
        return heading.group(1).strip()
    return path.stem


def _extract_links(text: str) -> list[str]:
    # Deliberate, narrow deviation from the MCP extractor: a fenced code
    # block is quotation (commands, samples), so links inside it are not
    # linkage. INLINE code spans are NOT stripped -- backtick-wrapped
    # wikilinks like `[[note]]` are an established rendering habit in real
    # vaults and removing them would cut live edges out of the graph.
    text = FENCED_BLOCK_RE.sub("", text)
    links: list[str] = []
    for match in WIKILINK_RE.findall(text):
        target = match.split("|", 1)[0].split("#", 1)[0].strip()
        if target:
            links.append(target)
    for match in MARKDOWN_LINK_RE.findall(text):
        target = match.strip()
        if "://" in target or target.startswith(("mailto:", "#")):
            continue
        target = target.split("#", 1)[0].split("?", 1)[0].strip()
        if target:
            links.append(target)
    return links


def scan(vault: Path, collect_adjacency: bool = False) -> dict:
    notes: dict[str, str] = {}
    titles: dict[str, str] = {}
    for path in sorted(vault.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in NOTE_EXTENSIONS:
            continue
        rel_parts = path.relative_to(vault).parts
        if any(part in SCAN_EXCLUDED_DIRS or part.startswith(".") for part in rel_parts[:-1]):
            continue
        if path.name.startswith("."):
            continue
        rel = path.relative_to(vault).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        notes[rel] = text
        titles[rel] = _extract_title(text, path)

    by_rel_lower = {rel.lower(): rel for rel in notes}
    lookup: dict[str, list[str]] = {}
    for rel in notes:
        keys = {
            _normalize_lookup(rel),
            _normalize_lookup(Path(rel).stem),
            _normalize_lookup(titles[rel]),
        }
        for key in keys:
            if key:
                lookup.setdefault(key, []).append(rel)

    def resolve(target: str, source_rel: str) -> tuple[str, str | None]:
        """Return (status, resolved_rel): note | excluded | broken."""
        cleaned = target.replace("\\", "/").strip().lstrip("./")
        candidates = [cleaned]
        if not cleaned.lower().endswith(NOTE_EXTENSIONS):
            candidates.extend(cleaned + ext for ext in NOTE_EXTENSIONS)
        source_dir = Path(source_rel).parent.as_posix()
        for candidate in list(candidates):
            joined = posixpath.normpath(posixpath.join(source_dir, candidate)) if source_dir != "." else candidate
            if joined not in candidates and not joined.startswith(".."):
                candidates.append(joined)
        for candidate in candidates:
            rel = by_rel_lower.get(candidate.lower())
            if rel:
                return "note", rel
        matches = lookup.get(_normalize_lookup(cleaned), [])
        if matches:
            return "note", sorted(matches)[0]
        for candidate in candidates:
            probe = (vault / candidate).resolve()
            try:
                probe.relative_to(vault.resolve())
            except ValueError:
                continue
            if probe.exists():
                return "excluded", None
        return "broken", None

    def moved_hint(target: str) -> str | None:
        """For a broken path-qualified target, a unique basename match is
        almost always the note's new home after a move/archive."""
        stem = _normalize_lookup(Path(target.replace("\\", "/")).stem)
        matches = sorted(set(lookup.get(stem, [])))
        return matches[0] if len(matches) == 1 else None

    outbound: dict[str, list[str]] = {}
    inbound: Counter[str] = Counter()
    broken: list[dict[str, str]] = []
    archived_broken: list[dict[str, str]] = []
    excluded_valid: list[dict[str, str]] = []
    adjacency: dict[str, list[str]] = {rel: [] for rel in notes} if collect_adjacency else {}
    total_links = 0
    for rel in sorted(notes):
        links = _extract_links(notes[rel])
        outbound[rel] = links
        total_links += len(links)
        generated = rel in GENERATED_SOURCES
        for target in links:
            status, resolved = resolve(target, rel)
            if status == "note" and resolved and resolved != rel:
                if not generated:
                    inbound[resolved] += 1
                if collect_adjacency:
                    adjacency[rel].append(resolved)
            elif status == "excluded":
                excluded_valid.append({"source": rel, "target": target})
            elif status == "broken":
                # Frozen history rots by design: a dead link whose SOURCE
                # is an archived note is bookkeeping, not an actionable
                # defect, and must not drown the live signal.
                bucket = archived_broken if "archive" in Path(rel).parts else broken
                entry = {"source": rel, "target": target}
                hint = moved_hint(target)
                if hint:
                    entry["hint"] = hint
                bucket.append(entry)

    orphans = sorted(
        rel
        for rel in notes
        if rel not in ORPHAN_EXEMPT
        and not rel.startswith(NON_NOTE_PREFIXES)
        and "archive" not in Path(rel).parts
        and not outbound[rel]
        and inbound[rel] == 0
    )
    hubs = [
        {"path": rel, "inbound": count}
        for rel, count in sorted(inbound.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "notes": len(notes),
        "links": total_links,
        "broken": sorted(broken, key=lambda entry: (entry["source"], entry["target"])),
        "archived_broken": sorted(archived_broken, key=lambda entry: (entry["source"], entry["target"])),
        "orphans": orphans,
        "excluded_valid": sorted(excluded_valid, key=lambda entry: (entry["source"], entry["target"])),
        "hubs": hubs,
        **({"adjacency": adjacency, "titles": titles} if collect_adjacency else {}),
    }


def build_context(vault: Path, note_ref: str, hops: int = 2, max_nodes: int = 10) -> dict:
    """The neighbourhood of one note on the wikilink graph: read-only BFS.

    Same resolution semantics as the map (and the MCP server): the
    reference may be a relative path, a unique stem, or a note title.
    Returns the starting node plus up to `max_nodes` neighbours, closest
    first, each carrying how it was reached, so an agent can pull the
    right two or three notes instead of guessing by name.
    """
    data = scan(vault, collect_adjacency=True)
    adjacency: dict[str, list[str]] = data.pop("adjacency")
    titles: dict[str, str] = data.pop("titles")

    target = note_ref.replace("\\", "/").strip()
    lowered = target.lower()
    keys = {lowered, lowered.removesuffix(".md")}
    start = None
    for rel in adjacency:
        if rel.lower() in keys or Path(rel).stem.lower() in keys:
            start = rel
            break
    if start is None:
        wanted = _normalize_lookup(target)
        for rel, title in titles.items():
            if _normalize_lookup(title) == wanted:
                start = rel
                break
    if start is None:
        raise FileNotFoundError(f"note not found: {note_ref}")

    inbound: dict[str, list[str]] = {}
    for src, dsts in adjacency.items():
        for dst in dsts:
            inbound.setdefault(dst, []).append(src)

    nodes: list[dict] = []
    seen = {start}
    frontier: list[tuple[str, str, str]] = [(start, "", "")]  # (rel, direction, via)
    depth = 0
    while frontier and depth <= hops and len(nodes) < max_nodes + 1:
        next_frontier: list[tuple[str, str, str]] = []
        for rel, direction, via in frontier:
            if len(nodes) >= max_nodes + 1:
                break
            nodes.append({
                "path": rel,
                "title": titles.get(rel, ""),
                "depth": depth,
                "direction": direction,
                "via": via,
            })
            seen.add(rel)
            for nxt in sorted(adjacency.get(rel, [])):
                if nxt not in seen:
                    next_frontier.append((nxt, "out", rel))
                    seen.add(nxt)
            for prv in sorted(inbound.get(rel, [])):
                if prv not in seen:
                    next_frontier.append((prv, "in", rel))
                    seen.add(prv)
        depth += 1
        frontier = next_frontier

    return {"context": start, "hops": hops, "max_nodes": max_nodes, "nodes": nodes[: max_nodes + 1]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexgen vault map", description=t("deterministic structural map of a Markdown vault (read-only)"))
    parser.add_argument("--vault", default=None, help=t("vault root directory"))
    parser.add_argument("--json", action="store_true", help=t("full machine-readable output"))
    parser.add_argument("--check", action="store_true", help=t("one summary line + broken list; always exit 0"))
    parser.add_argument("--top", type=int, default=10, help=t("hubs to show in the human report"))
    parser.add_argument("--context", default=None, metavar="NOTE",
                        help=t("the neighbourhood of one note (read-only graph BFS) instead of the whole map"))
    parser.add_argument("--hops", type=int, default=2, help=t("graph distance for --context (default 2)"))
    parser.add_argument("--max-nodes", dest="max_nodes", type=int, default=10,
                        help=t("maximum neighbours listed for --context (default 10)"))
    args = parser.parse_args(argv)

    vault = Path(args.vault).expanduser() if args.vault else resolve_vault_data()
    if not vault.is_dir():
        print(t("vault-map: not a directory: {vault}", vault=vault), file=sys.stderr)
        return 2

    if args.context:
        try:
            result = build_context(vault, args.context, hops=max(0, args.hops), max_nodes=max(1, args.max_nodes))
        except FileNotFoundError as exc:
            print(t("vault-map: {error}", error=exc), file=sys.stderr)
            return 3
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        print(t("context: {note} (hops={hops}, cap={cap})", note=result["context"], hops=result["hops"], cap=result["max_nodes"]))
        for node in result["nodes"]:
            if node["depth"] == 0:
                print(f"  {node['path']}" + (f" — {node['title']}" if node["title"] else ""))
                continue
            arrow = "->" if node["direction"] == "out" else "<-"
            via = t(" (via {via})", via=node["via"]) if node["via"] else ""
            print(f"  d{node['depth']} {arrow} {node['path']}" + (f" — {node['title']}" if node["title"] else "") + via)
        return 0

    data = scan(vault)

    if args.json:
        data["hubs"] = data["hubs"][: max(args.top, 10)]
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if args.check:
        print(
            f"notes={data['notes']} links={data['links']} "
            f"broken={len(data['broken'])} archived_broken={len(data['archived_broken'])} "
            f"orphans={len(data['orphans'])}"
        )
        for entry in data["broken"][:20]:
            print(f"broken: {entry['source']} -> [[{entry['target']}]]")
        if len(data["broken"]) > 20:
            print(f"broken: ... +{len(data['broken']) - 20} more")
        return 0

    print(t("vault-map: {notes} notes, {links} links", notes=data["notes"], links=data["links"]))
    print("\n" + t("broken links ({count}):", count=len(data["broken"])))
    for entry in data["broken"] or ():
        hint = t("  (probably moved to: {hint})", hint=entry["hint"]) if "hint" in entry else ""
        print(f"  {entry['source']} -> [[{entry['target']}]]{hint}")
    print("\n" + t("orphan notes ({count}):", count=len(data["orphans"])))
    for rel in data["orphans"]:
        print(f"  {rel}")
    print("\n" + t("top hubs (inbound):"))
    for entry in data["hubs"][: args.top]:
        print(f"  {entry['inbound']:3d} <- {entry['path']}")
    if data["archived_broken"]:
        print(
            "\n" + t(
                "dead links inside archived notes (frozen history, low priority): {count}",
                count=len(data["archived_broken"]),
            )
        )
    if data["excluded_valid"]:
        print("\n" + t("valid-but-excluded targets (e.g. 99-SECRETS, assets): {count}", count=len(data["excluded_valid"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

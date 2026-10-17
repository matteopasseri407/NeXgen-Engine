---
name: vault-map
description: Generate and explain the deterministic structural map of the KnowledgeVault - orphan notes, broken wikilinks, hub notes - and propose targeted fixes without applying them. Use when the user asks for the vault map, orphans, broken links, or how notes connect.
---

# Vault map

Read-only structural map of the vault's wikilink graph. The text after the
command, if any, is the request (e.g. a specific folder or question about
the structure).

1. The vault root needs no guessing: the tool resolves it itself, in this
   order: `AGENT_VAULT_DATA`, `KNOWLEDGE_VAULT_PATH`, then `~/KnowledgeVault`.
   Pass `--vault <root>` only to point it somewhere else.
2. Run the analyzer, read-only: `vault-map` (the installed command;
   `python3 03-INFRA/scripts/vault-map.py` is the same tool through the
   engine checkout, and on Windows use `python` or `py -3`).
   `--json` for programmatic use, `--check` for the one-line summary plus
   the broken links it found, `--context <note>` with `--hops` and
   `--max-nodes` for the neighbourhood of a single note.
3. Explain the result in plain language, not raw output:
   - **broken links**: what points where, and the likely cause (usually a
     renamed note); these are real defects worth fixing at the source.
   - **orphan notes**: notes nothing links to and that link nothing;
     candidates for an inbound pointer in `00-START-HERE.md` or for a
     grooming pass. Infrastructure files and exports are already filtered
     out by the tool - do not re-add them.
   - **hubs**: the gravitational centers of the vault, useful to orient.
4. Propose concrete fixes (link corrections through the vault-library
   write tools, `update_section` first when the fix fits one section;
   pointers in `00-START-HERE.md`) but NEVER apply them without the
   user's explicit confirmation in this conversation.
5. Renames, merges, archiving and any bulk cleanup belong to the guarded
   `vault-groom` flow - hand off there instead of improvising them here.

"""I testi che vault-groom manda ai runner LLM.

Dati, non codice: il giudizio di grooming vive nel playbook del vault
(`03-INFRA/vault-grooming-playbook.md`), questi sono solo gli involucri che
lo consegnano al runner in lettura o in scrittura. Tenerli qui, separati da
`groom.py`, evita di dover scorrere pagine di prosa per leggere
l'orchestrazione.
"""
from __future__ import annotations

PROPOSE_PROMPT_TEMPLATE = (
    "Read {playbook} and execute ONLY steps 1-3 (orient, run the audit "
    "heat-map, find candidates with semantic_search). ALSO run the "
    "structural map, `vault-map --vault {vault} --check`, and treat orphan "
    "notes and broken wikilinks it reports as first-class tranche "
    "candidates (orphan -> link-or-archive, broken link -> fix at the "
    "source). Then OUTPUT a proposed grooming tranche as a markdown table "
    "with EXACTLY these columns: | Note | Action | Why | -- one row per "
    "note, action is compress / merge / archive / fix-frontmatter / "
    "fix-link / no action, last column is one line of why. DO NOT edit, "
    "write, move, or commit anything -- this is a read-only planning pass."
)

WRITE_PROMPT_TEMPLATE = (
    "Read {playbook}. The user already reviewed and approved EXACTLY the "
    "grooming tranche recorded at {plan_record} (sha256 {tranche_hash}) -- "
    "that file is the single source of truth if anything below looks "
    "truncated or reformatted; the same text is reproduced here for "
    "convenience:\n\n"
    "---BEGIN APPROVED TRANCHE---\n"
    "{tranche}\n"
    "---END APPROVED TRANCHE---\n\n"
    "Execute precisely this tranche, nothing more and nothing less -- do "
    "not re-derive or expand it. Commit atomically per action with clear "
    "messages. Do NOT push -- pushing is decided separately after this run "
    "by mechanically checking what the commits actually touched, never by "
    "you. Before finishing, re-read the approved tranche row by row and "
    "end your response with an explicit checklist, one line per note that "
    "has a real action (skip rows marked \"no action\"): DONE (with the "
    "commit it landed in) or NOT DONE (with the concrete reason). Every "
    "actioned row must appear on that list -- do not let anything go "
    "unmentioned."
)


def build_propose_prompt(playbook: str, vault: str) -> str:
    """Il prompt della passata di sola lettura (preview e apply, step 1)."""
    return PROPOSE_PROMPT_TEMPLATE.format(playbook=playbook, vault=vault)


def build_write_prompt(playbook: str, plan_record: str, tranche_hash: str, tranche: str) -> str:
    """Il prompt del write pass: la tranche è quella approvata, non ri-derivata."""
    return WRITE_PROMPT_TEMPLATE.format(
        playbook=playbook,
        plan_record=plan_record,
        tranche_hash=tranche_hash,
        tranche=tranche,
    )

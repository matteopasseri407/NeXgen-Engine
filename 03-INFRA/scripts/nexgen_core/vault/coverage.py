"""Copertura della tranche approvata: cosa doveva essere toccato, cosa no.

Estratto da vault_groom_audit.py (release), invariato nella logica: parsing
best-effort della tabella `| Note | Action | Why |` che PROPOSE_PROMPT
richiede, poi un controllo bidirezionale contro i file davvero toccati dal
write pass.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

FILENAME_RE = re.compile(r"`([\w./-]+\.md)`")
NO_ACTION_RE = re.compile(r"nessuna azione|no action", re.IGNORECASE)
# Deliberatamente largo (combacia con "archive", "archivia", "archiviare",
# ...): la colonna Action è prosa libera, non un enum -- questo decide solo
# quali target sono ELEGIBILI per l'eccezione archive-move sotto, mai la
# copertura in sé.
ARCHIVE_ACTION_RE = re.compile(r"archivi|archive", re.IGNORECASE)

BACKLOG_NOTE = "99-INDEX/vault-cleanup-backlog.md"


def _under_archive_root(path: str, archive_root: str) -> bool:
    root = PurePosixPath(archive_root.strip("/"))
    p = PurePosixPath(path.strip("/"))
    return p == root or root in p.parents


def extract_action_targets(tranche_text: str) -> tuple[set[str], set[str], bool]:
    """Estrae i file che la tranche approvata si impegna a toccare.

    Righe la cui colonna Action dice "no action" non producono un target
    (mai attesi in files_touched) ma contano comunque come riga
    correttamente parsata (terzo valore di ritorno), così una tranche
    tutta "no action" non si confonde con una che non parsa affatto come
    tabella.

    Ritorna (targets, archive_targets, found_any_row). archive_targets è il
    sottoinsieme di targets la cui riga legge come un'azione di tipo
    archivio -- l'unico caso in cui l'eccezione archive-move sotto si
    applica.
    """
    targets: set[str] = set()
    archive_targets: set[str] = set()
    found_any_row = False
    for line in tranche_text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 2:
            continue
        note_col, action_col = cols[0], cols[1]
        row_targets = FILENAME_RE.findall(note_col)
        is_no_action = bool(NO_ACTION_RE.search(action_col))
        if row_targets or is_no_action:
            found_any_row = True
        if is_no_action:
            continue
        targets.update(row_targets)
        if row_targets and ARCHIVE_ACTION_RE.search(action_col):
            archive_targets.update(row_targets)
    return targets, archive_targets, found_any_row


def check_coverage(
    plan_record: str, files_touched: list[str], added_paths: set[str], archive_root: str
) -> dict:
    """Controllo bidirezionale: ogni target approvato è stato toccato, e
    nessun file fuori piano è stato toccato.

    Path-exact sul lato "il target è stato toccato": un target o compare
    verbatim in files_touched o è unaddressed, senza fallback sul basename.
    L'unica eccezione vive sull'altro lato: un path AGGIUNTO è scusato da
    out_of_scope se sta sotto `archive_root` E il suo basename combacia con
    quello (o la forma rinominata `<stem>-archive-<data>.md`, il pattern del
    playbook) di un target la cui riga è di tipo archivio.
    """
    plan_path = Path(plan_record)
    if not plan_path.is_file():
        return {
            "coverage_status": "clean",
            "unaddressed_targets": [],
            "matched_by_archive_move": [],
            "out_of_scope_targets": [],
        }

    tranche_text = plan_path.read_text(encoding="utf-8")
    targets, archive_targets, found_any_row = extract_action_targets(tranche_text)

    if tranche_text.strip() and not found_any_row:
        return {
            "coverage_status": "unparseable",
            "unaddressed_targets": [],
            "matched_by_archive_move": [],
            "out_of_scope_targets": [],
        }

    touched_set = set(files_touched)
    unaddressed = sorted(target for target in targets if target not in touched_set)

    archive_target_basenames = {Path(target).name for target in archive_targets}
    archive_target_stems = {Path(target).stem for target in archive_targets}

    def _is_archived_shape(name: str) -> bool:
        if name in archive_target_basenames:
            return True
        return any(name.startswith(stem + "-") for stem in archive_target_stems)

    matched_by_archive_move = sorted(
        path for path in added_paths
        if _under_archive_root(path, archive_root) and _is_archived_shape(Path(path).name)
    )
    sanctioned = set(matched_by_archive_move)
    out_of_scope = sorted(
        touched for touched in files_touched
        if touched not in targets and touched != BACKLOG_NOTE and touched not in sanctioned
    )

    status = "dirty" if (unaddressed or out_of_scope) else "clean"
    return {
        "coverage_status": status,
        "unaddressed_targets": unaddressed,
        "matched_by_archive_move": matched_by_archive_move,
        "out_of_scope_targets": out_of_scope,
    }

"""find_session non cerca opencode nel PATH quando le sessioni sono già note.

Su CI Linux (opencode non installato) i 4 test di find_session fallivano perché
la funzione chiamava _opencode_executable() prima di usare l'elenco finto passato
con lister.

Questo test simula l'assenza di opencode (shutil.which → None) e chiama
find_session con un lister finto: deve passare senza mai invocare
opencode_executable().
"""
from __future__ import annotations

from nexgen_local import supervision_costs as costs
from nexgen_local import supervision_harness as harness


def test_find_session_passa_con_opencode_assente_e_lister_finto(tmp_path, monkeypatch):
    """Senza opencode nel PATH, find_session con lister finto non fallisce."""
    monkeypatch.setattr(
        costs,
        "opencode_executable",
        lambda: (_ for _ in ()).throw(
            costs.CostError(
                "`opencode` non è nel PATH: impossibile leggere la sessione"
            )
        ),
    )
    folder = tmp_path / "copia"
    folder.mkdir()
    sessions = [
        {"id": "ses_nuova", "directory": str(folder), "created": 2_000_000},
        {"id": "ses_vecchia", "directory": str(folder), "created": 1_000_000},
    ]

    found = harness.find_session(
        folder, 1_500_000, wait_s=0, lister=lambda: sessions,
    )

    assert found is not None
    assert found["id"] == "ses_nuova"

"""vault-groom, ricostruito modulare.

`groom.py` orchestra (preview/apply), `gate.py` porta le regole di sicurezza
non negoziabili (clone senza remote, conferma umana, guardia anti-TOCTOU,
albero pulito), `runner.py` parla con i CLI LLM dietro un'interfaccia comune,
`prompts.py` tiene i testi lunghi separati dalla logica, `coverage.py` e
`audit.py` sono il gate di promozione verso il vault reale.
"""
from __future__ import annotations

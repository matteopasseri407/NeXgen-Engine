"""vault-groom, rebuilt as modules.

`groom.py` orchestrates (preview/apply), `gate.py` carries the
non-negotiable safety rules (remote-less clone, human confirmation,
anti-TOCTOU guard, clean tree), `runner.py` talks to the LLM CLIs behind a
common interface, `prompts.py` keeps the long texts separate from the
logic, `coverage.py` and `audit.py` are the promotion gate into the real
vault.
"""
from __future__ import annotations

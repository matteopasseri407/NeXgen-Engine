"""Configuration for the optional local lane.

The lane is deliberately self-describing: every root it may read, every
external command it may call, and every cap lives here. Nothing in this
module imports a framework, so the contract stays usable and testable
without LangGraph installed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: Default Ollama tag. Never hardcoded into behaviour, only into the fallback.
DEFAULT_MODEL = "gemma4-12b-openclaw:latest"

#: Parts that may never be read, whatever root is allowed.
EXCLUDED_PARTS = frozenset({"99-SECRETS", ".git", "node_modules", ".venv"})


def default_engine_root() -> Path:
    """The engine repository this lane reads by default.

    Resolved through the canonical engine resolver, never inferred from this
    file's position: inside an installed package the old inference returned
    ``env/lib``, and a second resolver is exactly the divergence NeXgen
    already paid for once. The resolver names the clone's ``03-INFRA``
    folder; its parent is the repository.
    """
    from nexgen_core.paths import resolve_engine_root

    return resolve_engine_root().resolve().parent


@dataclass(frozen=True)
class LaneConfig:
    """Everything the lane needs to know about its world."""

    vault_root: Path
    repo_roots: tuple[Path, ...] = ()
    model: str = DEFAULT_MODEL
    #: Per-node models: empty means "use `model`". The router can be a smaller
    #: model than the answerer; measured evidence says a 4B routes fine when
    #: the engine validates every path against real files.
    router_model: str = ""
    answer_model: str = ""
    num_ctx: int = 8192
    temperature: float = 0.0
    max_results: int = 5
    read_chars: int = 3000
    audit_path: Path = field(default_factory=lambda: Path.home() / ".local/state/nexgen/local-lane/audit.jsonl")
    proposals_dir: Path = field(
        default_factory=lambda: Path.home() / ".local/state/nexgen/local-lane/proposals"
    )
    drafts_dir: Path = field(default_factory=lambda: Path.home() / ".local/state/nexgen/local-lane/drafts")
    firecrawl_cmd: str = "firecrawl-local"
    pdftotext_cmd: str = "pdftotext"
    max_steps: int = 4
    excluded_parts: frozenset[str] = EXCLUDED_PARTS

    @property
    def router_tag(self) -> str:
        return self.router_model or self.model

    @property
    def answer_tag(self) -> str:
        return self.answer_model or self.model

    @classmethod
    def from_env(
        cls,
        *,
        vault: str | Path | None = None,
        repos: tuple[str | Path, ...] | None = None,
        model: str | None = None,
        audit: str | Path | None = None,
        router_model: str | None = None,
        answer_model: str | None = None,
    ) -> "LaneConfig":
        vault_root = Path(
            vault or os.environ.get("AGENT_VAULT_DATA") or (Path.home() / "KnowledgeVault")
        ).expanduser().resolve()
        if repos:
            repo_roots = tuple(Path(p).expanduser().resolve() for p in repos)
        else:
            engine = default_engine_root()
            repo_roots = (engine,) if engine.is_dir() else ()
        audit_path = Path(
            audit or os.environ.get("NEXGEN_LOCAL_AUDIT") or (Path.home() / ".local/state/nexgen/local-lane/audit.jsonl")
        ).expanduser().resolve()
        return cls(
            vault_root=vault_root,
            repo_roots=repo_roots,
            model=model or os.environ.get("NEXGEN_LOCAL_MODEL") or DEFAULT_MODEL,
            router_model=router_model or os.environ.get("NEXGEN_LOCAL_ROUTER_MODEL") or "",
            answer_model=answer_model or os.environ.get("NEXGEN_LOCAL_ANSWER_MODEL") or "",
            audit_path=audit_path,
        )

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

from nexgen_core.files import secure_artifact as secure_artifact
from nexgen_core.paths import resolve_home, resolve_vault_data

#: Default Ollama tag. Never hardcoded into behaviour, only into the fallback.
DEFAULT_MODEL = "gemma4-12b-openclaw:latest"

#: Parts that may never be read, whatever root is allowed.
EXCLUDED_PARTS = frozenset({"99-SECRETS", ".git", "node_modules", ".venv"})


def lane_state_dir() -> Path:
    """Where the lane keeps its audit log, proposals, drafts and research sessions.

    Under the engine's home (`NEXGEN_HOME` when set), the same place every other piece of engine state
    follows. These defaults used the process's real home, so a checkout run in a sandbox home beside a
    working installation wrote its proposals and audit trail into the working installation's lane.
    """
    return resolve_home() / ".local/state/nexgen/local-lane"


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
    #: Context window per call. 64K is ample for lane prompts plus thinking
    #: traces and loads fast; raise via NEXGEN_LOCAL_NUM_CTX up to the
    #: measured full-GPU ceilings (12B: 172032, 4B: 217088).
    num_ctx: int = 65536
    temperature: float = 0.0
    max_results: int = 5
    read_chars: int = 3000
    audit_path: Path = field(default_factory=lambda: lane_state_dir() / "audit.jsonl")
    proposals_dir: Path = field(
        default_factory=lambda: lane_state_dir() / "proposals"
    )
    drafts_dir: Path = field(default_factory=lambda: lane_state_dir() / "drafts")
    mails_dir: Path = field(default_factory=lambda: lane_state_dir() / "mails")
    uploads_dir: Path = field(default_factory=lambda: lane_state_dir() / "uploads")
    calendars_dir: Path = field(default_factory=lambda: lane_state_dir() / "calendars")
    workflows_dir: Path = field(default_factory=lambda: lane_state_dir() / "workflows")
    #: Persistent research sessions (one sqlite each): sources, chunks read,
    #: coverage and staged proposals across interactions. Working state only,
    #: swept by age; never vault memory.
    research_dir: Path = field(default_factory=lambda: lane_state_dir() / "research")
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
        # The one resolver: it also reads KNOWLEDGE_VAULT_PATH, which this lane ignored.
        vault_root = resolve_vault_data(None, Path(vault) if vault else None).resolve()
        if repos:
            repo_roots = tuple(Path(p).expanduser().resolve() for p in repos)
        else:
            engine = default_engine_root()
            repo_roots = (engine,) if engine.is_dir() else ()
        audit_path = Path(
            audit or os.environ.get("NEXGEN_LOCAL_AUDIT") or (lane_state_dir() / "audit.jsonl")
        ).expanduser().resolve()
        try:
            num_ctx = int(os.environ.get("NEXGEN_LOCAL_NUM_CTX") or 0) or 65536
        except ValueError:
            num_ctx = 65536
        return cls(
            vault_root=vault_root,
            repo_roots=repo_roots,
            model=model or os.environ.get("NEXGEN_LOCAL_MODEL") or DEFAULT_MODEL,
            router_model=router_model or os.environ.get("NEXGEN_LOCAL_ROUTER_MODEL") or "",
            answer_model=answer_model or os.environ.get("NEXGEN_LOCAL_ANSWER_MODEL") or "",
            num_ctx=num_ctx,
            audit_path=audit_path,
        )

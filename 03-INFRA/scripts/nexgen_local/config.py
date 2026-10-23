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

from .tiers import DEFAULT_TIER, TIERS, tier_for_model

#: Default Ollama tag. Never hardcoded into behaviour, only into the fallback.
DEFAULT_MODEL = "gemma4-12b-openclaw:latest"

#: Parts that may never be read, whatever root is allowed.
EXCLUDED_PARTS = frozenset({"99-SECRETS", ".git", "node_modules", ".venv"})


def _lane_state_dir_impl() -> Path:
    return resolve_home() / ".local/state/nexgen/local-lane"


def lane_state_dir() -> Path:
    """Where the lane keeps its audit log, proposals, drafts and research sessions.

    Under the engine's home (`NEXGEN_HOME` when set), the same place every other piece of engine state
    follows. These defaults used the process's real home, so a checkout run in a sandbox home beside a
    working installation wrote its proposals and audit trail into the working installation's lane.
    """
    return _lane_state_dir_impl()


def _default_engine_root_impl() -> Path:
    from nexgen_core.paths import resolve_engine_root

    return resolve_engine_root().resolve().parent


def default_engine_root() -> Path:
    """The engine repository this lane reads by default.

    Resolved through the canonical engine resolver, never inferred from this
    file's position: inside an installed package the old inference returned
    ``env/lib``, and a second resolver is exactly the divergence NeXgen
    already paid for once. The resolver names the clone's ``03-INFRA``
    folder; its parent is the repository.
    """
    return _default_engine_root_impl()


def _lane_config_from_env_impl(
    cls,
    *,
    vault: str | Path | None = None,
    repos: tuple[str | Path, ...] | None = None,
    model: str | None = None,
    audit: str | Path | None = None,
    router_model: str | None = None,
    answer_model: str | None = None,
    tier: str | None = None,
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
    resolved_model = model or os.environ.get("NEXGEN_LOCAL_MODEL") or DEFAULT_MODEL
    # An explicit tier wins; otherwise the stored per-model map decides;
    # anything unknown degrades to tutor, never to a guess.
    requested = (tier or os.environ.get("NEXGEN_LOCAL_TIER") or "").strip()
    resolved_tier = requested if requested in TIERS else tier_for_model(resolved_model, load_model_tiers())
    return cls(
        vault_root=vault_root,
        repo_roots=repo_roots,
        model=resolved_model,
        router_model=router_model or os.environ.get("NEXGEN_LOCAL_ROUTER_MODEL") or "",
        answer_model=answer_model or os.environ.get("NEXGEN_LOCAL_ANSWER_MODEL") or "",
        num_ctx=num_ctx,
        audit_path=audit_path,
        tier=resolved_tier,
    )


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
    uploads_dir: Path = field(default_factory=lambda: lane_state_dir() / "uploads")
    firecrawl_cmd: str = "firecrawl-local"
    pdftotext_cmd: str = "pdftotext"
    excluded_parts: frozenset[str] = EXCLUDED_PARTS
    #: Capability tier of the model in use: tutor, guided or assisted. Unknown
    #: models and unknown names resolve to tutor, the strictest tier.
    tier: str = DEFAULT_TIER

    def __post_init__(self) -> None:
        if self.tier not in TIERS:
            raise ValueError(f"tier sconosciuto: {self.tier!r} (attesi: {', '.join(TIERS)})")

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
        tier: str | None = None,
    ) -> "LaneConfig":
        return _lane_config_from_env_impl(
            cls,
            vault=vault,
            repos=repos,
            model=model,
            audit=audit,
            router_model=router_model,
            answer_model=answer_model,
            tier=tier,
        )


def _model_tiers_path_impl() -> Path:
    from .tiers import tiers_file

    return tiers_file()


def model_tiers_path() -> Path:
    """Where the per-model tier map lives: the lane's own state directory."""
    return _model_tiers_path_impl()


def _load_model_tiers_impl(path: str | Path | None = None) -> dict[str, str]:
    from .tiers import tiers_file

    target = Path(path).expanduser() if path else tiers_file()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        import json

        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(model): str(name) for model, name in parsed.items() if isinstance(model, str)}


def load_model_tiers(path: str | Path | None = None) -> dict[str, str]:
    """The stored per-model tiers; missing or corrupt files mean no map.

    Tolerant on purpose: at the config boundary an unreadable map must degrade
    to tutor for every model, not abort the lane.
    """
    return _load_model_tiers_impl(path)


def _save_model_tier_impl(model: str, tier: str, path: str | Path | None = None) -> Path:
    from .tiers import tiers_file

    name = str(model or "").strip()
    if not name:
        raise ValueError("modello vuoto: indica il tag del modello da assegnare")
    if tier not in TIERS:
        raise ValueError(f"tier sconosciuto: {tier!r} (attesi: {', '.join(TIERS)})")
    target = Path(path).expanduser() if path else tiers_file()
    mapping = load_model_tiers(target)
    mapping[name] = tier
    target.parent.mkdir(parents=True, exist_ok=True)
    import json

    target.write_text(json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def save_model_tier(model: str, tier: str, path: str | Path | None = None) -> Path:
    """Store one model's tier; the explicit approval step after an evaluation.

    Raises ValueError on an empty model or an unknown tier instead of writing
    a map the lane would silently read as tutor.
    """
    return _save_model_tier_impl(model, tier, path)

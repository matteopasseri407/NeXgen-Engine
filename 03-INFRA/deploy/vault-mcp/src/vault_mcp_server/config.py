from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


DEFAULT_IGNORED_DIRS = (
    ".git",
    ".obsidian",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
)


def _get_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    parsed = int(raw_value)
    if parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return parsed


def _get_csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    values = tuple(part.strip() for part in raw_value.split(",") if part.strip())
    return values or default


def _normalize_route(value: str) -> str:
    stripped = value.strip()
    if not stripped or stripped == "/":
        return "/"
    return "/" + stripped.strip("/")


@dataclass(frozen=True, slots=True)
class Settings:
    vault_root: Path
    vault_token: str | None
    write_enabled: bool
    git_dir: Path | None
    git_author_name: str
    git_author_email: str
    host: str
    port: int
    mcp_path: str
    health_path: str
    stateless_http: bool
    json_response: bool
    allowed_origins: tuple[str, ...]
    ignored_dirs: tuple[str, ...]
    max_note_bytes: int
    cache_ttl_seconds: int
    default_search_limit: int
    max_search_limit: int
    start_here_filename: str
    include_path_prefixes: tuple[str, ...]
    exclude_path_prefixes: tuple[str, ...]
    write_exclude_path_prefixes: tuple[str, ...]
    max_write_bytes: int
    semantic_url: str | None
    semantic_enabled: bool
    semantic_max_limit: int

    @classmethod
    def from_env(cls) -> "Settings":
        raw_root = os.getenv("VAULT_ROOT")
        if not raw_root:
            raise ValueError("VAULT_ROOT is required")

        vault_root = Path(raw_root).expanduser().resolve()
        if not vault_root.exists():
            raise ValueError(f"VAULT_ROOT does not exist: {vault_root}")
        if not vault_root.is_dir():
            raise ValueError(f"VAULT_ROOT is not a directory: {vault_root}")

        vault_token = os.getenv("VAULT_TOKEN", "").strip() or None
        write_enabled = _get_bool("VAULT_WRITE_ENABLED", False)
        raw_git_dir = os.getenv("VAULT_GIT_DIR", "").strip()
        git_dir = Path(raw_git_dir).expanduser().resolve() if raw_git_dir else None

        if write_enabled:
            if git_dir is None:
                raise ValueError("VAULT_GIT_DIR is required when VAULT_WRITE_ENABLED=true")
            if not git_dir.exists():
                raise ValueError(f"VAULT_GIT_DIR does not exist: {git_dir}")
            if not git_dir.is_dir():
                raise ValueError(f"VAULT_GIT_DIR is not a directory: {git_dir}")

        default_search_limit = _get_int("DEFAULT_SEARCH_LIMIT", 10)
        max_search_limit = _get_int(
            "MAX_SEARCH_LIMIT",
            max(default_search_limit, 25),
        )
        if default_search_limit > max_search_limit:
            raise ValueError("DEFAULT_SEARCH_LIMIT cannot be greater than MAX_SEARCH_LIMIT")

        return cls(
            vault_root=vault_root,
            vault_token=vault_token,
            write_enabled=write_enabled,
            git_dir=git_dir,
            git_author_name=os.getenv("VAULT_GIT_AUTHOR_NAME", "Vault MCP").strip() or "Vault MCP",
            git_author_email=os.getenv("VAULT_GIT_AUTHOR_EMAIL", "vault-mcp@localhost").strip()
            or "vault-mcp@localhost",
            host=os.getenv("MCP_HOST", "0.0.0.0"),
            port=_get_int("MCP_PORT", 8080),
            mcp_path=_normalize_route(os.getenv("MCP_PATH", "/mcp")),
            health_path=_normalize_route(os.getenv("HEALTH_PATH", "/healthz")),
            stateless_http=_get_bool("MCP_STATELESS_HTTP", True),
            json_response=_get_bool("MCP_JSON_RESPONSE", True),
            allowed_origins=_get_csv("ALLOWED_ORIGINS"),
            ignored_dirs=_get_csv("IGNORE_DIRS", DEFAULT_IGNORED_DIRS),
            max_note_bytes=_get_int("MAX_NOTE_BYTES", 1_048_576),
            cache_ttl_seconds=_get_int("CACHE_TTL_SECONDS", 10),
            default_search_limit=default_search_limit,
            max_search_limit=max_search_limit,
            start_here_filename=os.getenv("START_HERE_FILENAME", "00-START-HERE.md").strip()
            or "00-START-HERE.md",
            include_path_prefixes=_get_csv("INCLUDE_PATH_PREFIXES"),
            exclude_path_prefixes=_get_csv("EXCLUDE_PATH_PREFIXES"),
            write_exclude_path_prefixes=_get_csv("WRITE_EXCLUDE_PATH_PREFIXES", ("99-SECRETS", ".git")),
            max_write_bytes=_get_int("MAX_WRITE_BYTES", 262_144),
            semantic_url=os.getenv("SEMANTIC_URL", "").strip() or None,
            semantic_enabled=_get_bool("SEMANTIC_ENABLED", False),
            semantic_max_limit=_get_int("SEMANTIC_MAX_LIMIT", 10),
        )

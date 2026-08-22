"""The services that make up a complete installation, declared once.

These are the five connectors the manifest marks `tier: core`. Four are
containers that can be hosted either on a server or on the user's own
machine; the fifth, the browser, always runs locally and has no stack to
start.

This table exists because until now the only way to get them was a script
that declared "Run ON the VPS" and configured a server's firewall. On a
single machine there was nothing to run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Service:
    """A stack service, with what's needed to start it and to find it."""

    name: str
    stack: str
    """Subfolder of 03-INFRA/deploy that holds its docker-compose.yml."""
    container: str
    port: int
    """The port on 127.0.0.1 that compose publishes it on."""
    exports: dict[str, str] = field(default_factory=dict)
    """The variables the workstation needs to have to mount the connector.

    `{port}` is replaced with the real port, `{token:NAME}` with the
    secret of that name taken from the stack's .env file.
    """
    requires_secret: tuple[str, ...] = ()
    """Secrets that must exist before starting: they generate themselves."""


#: The four containers. The order is the startup order: whatever depends on
#: nothing starts first, so a failure shows up right away, not halfway through.
SERVICES: tuple[Service, ...] = (
    Service(
        name="firecrawl",
        stack="firecrawl",
        container="firecrawl-api",
        port=3002,
        exports={"FIRECRAWL_TUNNEL_PORT": "{port}", "FIRECRAWL_API_URL": "http://127.0.0.1:{port}"},
        requires_secret=("FIRECRAWL_REDIS_PASSWORD", "FIRECRAWL_NUQ_POSTGRES_PASSWORD"),
    ),
    Service(
        name="vault-ocr",
        stack="ocr",
        container="vault-ocr-api",
        port=3033,
        exports={"OCR_TUNNEL_PORT": "{port}", "VAULT_OCR_API_URL": "http://127.0.0.1:{port}"},
    ),
    Service(
        name="n8n",
        stack="n8n",
        container="n8n",
        port=5678,
        exports={"N8N_TUNNEL_PORT": "{port}", "N8N_MCP_TOKEN": "{token:N8N_MCP_TOKEN}"},
        requires_secret=("N8N_ENCRYPTION_KEY", "N8N_MCP_TOKEN"),
    ),
    Service(
        name="vault-library",
        stack="vault-mcp",
        container="vault-mcp",
        port=8081,
        exports={
            "VAULT_LIBRARY_URL": "http://127.0.0.1:{port}/mcp",
            "VAULT_LIBRARY_TOKEN": "{token:VAULT_LIBRARY_TOKEN}",
        },
        requires_secret=("VAULT_LIBRARY_TOKEN",),
    ),
)

#: The fifth core connector. It has no container: it lives on the machine
#: of whoever uses it, and it's named here only so "the five essentials"
#: stays true.
LOCAL_ONLY_CONNECTORS = ("playwright",)


def deploy_root(engine_root: Path) -> Path:
    """The folder that holds the stacks, inside the installed engine."""
    return Path(engine_root) / "deploy"


def compose_file(engine_root: Path, service: Service) -> Path:
    return deploy_root(engine_root) / service.stack / "docker-compose.yml"


def env_file(engine_root: Path) -> Path:
    """The secrets file shared by the stacks. Never goes into git."""
    return deploy_root(engine_root) / ".env"


def by_name(name: str) -> Service | None:
    for service in SERVICES:
        if service.name == name:
            return service
    return None

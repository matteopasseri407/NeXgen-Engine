"""I servizi che compongono un'installazione completa, dichiarati una volta.

Sono i cinque connettori che il manifest marca `tier: core`. Quattro sono
container che si possono ospitare o su un server o sulla propria macchina;
il quinto, il browser, gira sempre in locale e non ha uno stack da avviare.

Questa tabella esiste perché finora l'unico modo di ottenerli era uno script
che dichiarava "Run ON the VPS" e configurava il firewall di un server. Su una
macchina sola non c'era niente da eseguire.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Service:
    """Un servizio dello stack, con ciò che serve per avviarlo e per trovarlo."""

    name: str
    stack: str
    """Sottocartella di 03-INFRA/deploy che contiene il suo docker-compose.yml."""
    container: str
    port: int
    """La porta su 127.0.0.1 a cui il compose lo pubblica."""
    exports: dict[str, str] = field(default_factory=dict)
    """Le variabili che la workstation deve avere per montare il connettore.

    `{porta}` viene sostituita con la porta reale, `{token:NOME}` con il
    segreto di quel nome preso dal file .env dello stack.
    """
    requires_secret: tuple[str, ...] = ()
    """Segreti che devono esistere prima di avviare: si generano da soli."""


#: I quattro container. L'ordine è quello di avvio: chi non dipende da nessuno
#: parte per primo, così un errore si vede subito e non a metà.
SERVICES: tuple[Service, ...] = (
    Service(
        name="firecrawl",
        stack="firecrawl",
        container="firecrawl-api",
        port=3002,
        exports={"FIRECRAWL_TUNNEL_PORT": "{porta}", "FIRECRAWL_API_URL": "http://127.0.0.1:{porta}"},
        requires_secret=("FIRECRAWL_REDIS_PASSWORD", "FIRECRAWL_NUQ_POSTGRES_PASSWORD"),
    ),
    Service(
        name="vault-ocr",
        stack="ocr",
        container="vault-ocr-api",
        port=3033,
        exports={"OCR_TUNNEL_PORT": "{porta}", "VAULT_OCR_API_URL": "http://127.0.0.1:{porta}"},
    ),
    Service(
        name="n8n",
        stack="n8n",
        container="n8n",
        port=5678,
        exports={"N8N_TUNNEL_PORT": "{porta}", "N8N_MCP_TOKEN": "{token:N8N_MCP_TOKEN}"},
        requires_secret=("N8N_ENCRYPTION_KEY", "N8N_MCP_TOKEN"),
    ),
    Service(
        name="vault-library",
        stack="vault-mcp",
        container="vault-mcp",
        port=8081,
        exports={
            "VAULT_LIBRARY_URL": "http://127.0.0.1:{porta}/mcp",
            "VAULT_LIBRARY_TOKEN": "{token:VAULT_LIBRARY_TOKEN}",
        },
        requires_secret=("VAULT_LIBRARY_TOKEN",),
    ),
)

#: Il quinto connettore core. Non ha container: vive sulla macchina di chi lo
#: usa, e lo si nomina qui solo perché "i cinque essenziali" resti vero.
LOCAL_ONLY_CONNECTORS = ("playwright",)


def deploy_root(engine_root: Path) -> Path:
    """La cartella che contiene gli stack, dentro il motore installato."""
    return Path(engine_root) / "deploy"


def compose_file(engine_root: Path, service: Service) -> Path:
    return deploy_root(engine_root) / service.stack / "docker-compose.yml"


def env_file(engine_root: Path) -> Path:
    """Il file di segreti condiviso dagli stack. Non entra mai in git."""
    return deploy_root(engine_root) / ".env"


def by_name(name: str) -> Service | None:
    for service in SERVICES:
        if service.name == name:
            return service
    return None

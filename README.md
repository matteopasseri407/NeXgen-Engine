# NeXgen Vault (AgentOps Governance Framework)

Most companies struggle to move AI agents from individual laptops to structured workflows. This repository provides the infrastructure and governance layer to run AI agents (Claude, Gemini, DeepSeek) predictably and safely.

Instead of hiding data in proprietary vector databases or relying on black-box autonomous swarms, NeXgen Vault uses standard Git repositories, predictable scripts, and strict compliance checks to keep operations under control.

## Core concepts

- **Infrastructure as Code for AI.** Manifest files define tools, permissions, and agent behaviors. A generator script creates the correct configuration for different CLIs.
- **Git-backed memory.** The agents read and write Markdown files. Every change is version-controlled, diffable, and easy to revert.
- **Continuous compliance.** In MULTI profile, the `agent-doctor` script checks over 30 system parameters before letting agents run. It blocks processes if it detects configuration drift or exposed credentials. In MINIMAL, there is no doctor: a single CLI on a single machine is verified visually.
- **Cross-platform consistency (optional).** In MULTI profile, the system forces agents to behave identically across different machines (e.g., a Windows workstation and a Linux laptop) through a provisioner. In MINIMAL, there is only one machine, so the provisioner is a no-op and is not installed.

## Architecture: The Three Planes

NeXgen Vault separates operations into three distinct planes:

1. **Behavior:** A single operating policy (`AGENTS.md`) linked into every runtime.
2. **Configuration:** An abstract MCP manifest compiled into each CLI's specific dialect by a generator script.
3. **Memory:** A plain-Markdown vault, written through serialized paths. 

Writes go through one door per kind of thing. Knowledge notes are written only through a memory tool server that serializes with a lock and an expected-hash check, preventing agents from overwriting each other's work.

## Shared Tools via MCP (Modular & Free-Tier Ready)

Agents share infrastructure rather than reinventing it. A few services run once, and every agent reaches them over the Model Context Protocol (MCP):

> **Note:** These specific tools are completely interchangeable. They were selected because they run comfortably and at zero cost on an **Oracle Cloud Always Free VPS** (4 ARM Ampere cores, 24GB RAM, 200GB SSD). You can easily swap them for enterprise equivalents.

- **Semantic Search:** A self-hosted retrieval layer (static embeddings via model2vec + BM25) runs CPU-only on a private VPS. Agents query the knowledge base by meaning without sending internal data to cloud models.
- **Web Scraping:** A self-hosted Firecrawl instance serves as the default read-only lane.
- **Local OCR:** A self-hosted OCR service extracts text from screenshots, logs, and scanned documents locally.
- **Visible Browser:** For interactive tasks (forms, logins, page checks), agents attach to a real, visible Chrome window via the DevTools protocol. **Agents are strictly forbidden from running headless browsers behind the user's back.**

## What We Deliberately Didn't Build

We didn't write a proprietary memory engine. Markdown, Git, and a simple tool server already provide durable, auditable memory that humans and agents can both read. 
There are no complex "agent-to-agent negotiations", no autonomous Swarm A* planners, no CRDTs, and no secondary databases. The effort went entirely into the layer *above* storage: the operational governance and safety rails.

## What's inside

| Directory | Purpose |
|---|---|
| `03-INFRA/` | The engine. Contains the agent bootstrap rules (`AGENTS.md`), MCP server definitions, and validation scripts (`agent-sync`, `agent-doctor`). |
| `99-INDEX/` | The identity layer. Tells agents about the current hardware, operating system, and deployment context (`USER-PROFILE.md`). |
| `01-NOTES/` | Standard workspace for documentation. |
| `02-PROJECTS/` | Project tracking and execution logs. |
| `04-NOW/` | Active priorities. This restricts agents from wandering into irrelevant tasks. |

## Deployment modes

1. **Local-Only.** Runs entirely on your machine. Relies on native CLI tools and local models. Good for testing and single-user setups.
2. **Cloud-Server.** Connects to a self-hosted stack (like n8n for orchestration, Firecrawl for scraping, and dedicated OCR) over an SSH tunnel. Built for production workflows.

The AI-guided setup (`INIT.md`) configures the correct mode for your environment.

## Installation profiles

The framework fits two shapes of usage. The installer (`INIT.md`) asks and picks the right one.

- **MINIMAL.** One CLI on one machine (e.g., only Claude Code on your laptop, or [OpenCode](https://opencode.ai/go?ref=RK9MPMS1TB) for a DeepSeek-based single-CLI setup). You get the knowledge vault, the bootstrap rules, lazy skills, and the discipline of writing memory through one door. There is no provisioner to run, no doctor to schedule, no cross-machine sync. Mount the MCP servers and skills you want directly in your CLI by hand. Best for solo users who just want AgentOps governance on top of a single agent.
- **MULTI.** Two or more CLIs and/or two or more machines. The provisioner (`agent-sync`), the generator (`render.py`), the doctor, and the healthcheck come online and keep every CLI and machine aligned to the canonical source in the vault. Best for a workstation + laptop setup, or for running multiple CLIs side by side.

You can start MINIMAL and switch to MULTI later. The canonical files in the vault do not change between profiles.

## Installation

You don't need to fill out configuration files manually.

1. Clone the repository:
   ```bash
   git clone https://github.com/matteopasseri407/NeXgen-Vault-OL.git ~/KnowledgeVault
   cd ~/KnowledgeVault
   ```
   > Optional preflight: `bash install.sh` — checks prerequisites, verifies the scaffold, detects your CLIs, and prints the next step. It writes nothing and is safe to re-run.
2. Open `INIT.md`.
3. Paste its contents into a **filesystem-capable agent CLI** (Claude Code, Codex, OpenCode, Antigravity) opened in this folder — not a plain web chat (claude.ai / gemini), which cannot write files.
4. The agent will ask how many CLIs and machines you have, your hardware, and your deployment mode, then configure the vault automatically.

## Prerequisites

- Git
- Python 3 with PyYAML (`pip install pyyaml`)
- Node.js (for `npx`, needed if you mount MCP servers or external skills)
- Optional: [OpenCode](https://opencode.ai/go?ref=RK9MPMS1TB) as one of the supported CLIs
- `jq` and `curl` on Linux/Mac (only needed for the MULTI profile sync and health scripts)

## Platform status

Linux is the daily-driven platform and the most tested. Windows support is an early preview, actively being worked on: the MULTI-profile PowerShell scripts (`agent-sync.ps1`, `agent-doctor.ps1`) run, but the MCP config generator (`render.py`) does not have a Windows dialect yet, and a couple of runtime paths (e.g. where Antigravity reads its instructions file) are inferred by analogy with Linux rather than confirmed live. MINIMAL profile is the safer starting point on Windows today. macOS follows the Linux code paths but has seen less real-world use.

## License

See `LICENSE`.

---

# NeXgen Vault (Framework AgentOps) — Italiano

Portare gli agenti AI dai portatili dei singoli sviluppatori a un flusso di lavoro aziendale strutturato è difficile. Questo repository fornisce l'infrastruttura e i controlli necessari per far operare agenti AI (Claude, Gemini, DeepSeek) in modo sicuro e prevedibile.

Invece di nascondere i dati in database proprietari o affidarsi a sciami autonomi incontrollabili, NeXgen Vault usa repository Git standard, script leggibili e controlli di conformità rigorosi per mantenere la governance sulle operazioni.

## Concetti base

- **Infrastruttura come codice per l'AI.** I file manifest definiscono tool, permessi e regole di comportamento. Uno script genera poi la configurazione corretta per le diverse CLI.
- **Memoria basata su Git.** Gli agenti leggono e scrivono file Markdown. Ogni modifica è tracciata, verificabile e facile da annullare.
- **Conformità continua.** Nel profilo MULTI lo script `agent-doctor` verifica oltre 30 parametri di sistema prima di far partire gli agenti. Blocca i processi se rileva configurazioni alterate o credenziali esposte. In MINIMAL non c'è doctor: una CLI su una macchina si verifica a vista.
- **Coerenza tra macchine (opzionale).** Nel profilo MULTI il sistema forza gli agenti a comportarsi in modo identico su hardware diverso (ad esempio, una workstation Windows e un portatile Linux) tramite un provisioner. In MINIMAL c'è una sola macchina, quindi il provisioner è no-op e non viene installato.

## Architettura: I Tre Piani

NeXgen Vault separa le operazioni in tre piani distinti:

1. **Comportamento:** Una singola policy operativa (`AGENTS.md`) collegata a ogni runtime.
2. **Configurazione:** Un manifest MCP astratto, compilato nei dialetti specifici di ogni CLI da uno script generatore.
3. **Memoria:** Un vault in puro Markdown, scritto tramite percorsi serializzati.

Le scritture passano attraverso una sola porta per tipologia. Le note vengono scritte esclusivamente tramite un tool server che serializza le richieste con un lock e un controllo sull'hash atteso, impedendo agli agenti di sovrascrivere il lavoro altrui.

## Tool Condivisi tramite MCP (Modulari e ottimizzati per Free-Tier)

Gli agenti condividono l'infrastruttura invece di reinventarla. Alcuni servizi girano in singola istanza e tutti gli agenti vi accedono tramite Model Context Protocol (MCP):

> **Nota importante:** Questi tool specifici sono completamente intercambiabili. Sono stati scelti perché girano comodamente e a costo zero su una **VPS Oracle Cloud Always Free** (4 core ARM Ampere, 24GB di RAM, 200GB di SSD). Possono essere sostituiti con alternative Enterprise in base alle necessità.

- **Ricerca Semantica:** Un livello di retrieval self-hosted (embedding statici via model2vec + BM25) gira in CPU-only su una VPS privata. Gli agenti interrogano la knowledge base per significato senza inviare dati interni a modelli cloud.
- **Web Scraping:** Un'istanza Firecrawl self-hosted funge da corsia read-only predefinita.
- **OCR Locale:** Un servizio OCR self-hosted estrae testo da screenshot, log e documenti scansionati localmente.
- **Browser Visibile:** Per i task interattivi (form, login, controlli su pagine), gli agenti si collegano a una finestra Chrome reale e visibile tramite protocollo DevTools. **Agli agenti è severamente vietato eseguire browser headless all'insaputa dell'utente.**

## Cosa NON abbiamo costruito (di proposito)

Non abbiamo scritto un motore di memoria proprietario. Markdown, Git e un semplice tool server offrono già una memoria durevole e auditabile che umani e agenti possono leggere. 
Non ci sono complesse "negoziazioni tra agenti", né pianificatori Swarm A* autonomi, né CRDT, né database secondari. Lo sforzo è andato interamente sul livello *sopra* lo storage: la governance operativa e i binari di sicurezza.

## Contenuto

| Directory | Scopo |
|---|---|
| `03-INFRA/` | Il motore. Contiene le regole base (`AGENTS.md`), le definizioni dei server MCP e gli script di validazione (`agent-sync`, `agent-doctor`). |
| `99-INDEX/` | Il livello di identità. Informa gli agenti sull'hardware, il sistema operativo e il contesto attuale (`USER-PROFILE.md`). |
| `01-NOTES/` | Spazio di lavoro standard per la documentazione. |
| `02-PROJECTS/` | Tracciamento dei progetti e log operativi. |
| `04-NOW/` | Priorità attive. Evita che gli agenti si disperdano su task non rilevanti. |

## Modalità di deployment

1. **Locale.** Gira interamente sulla tua macchina. Usa i tool nativi delle CLI e modelli locali. Adatto per test e setup mono-utente.
2. **Cloud-Server.** Si collega a uno stack remoto (come n8n per l'orchestrazione, Firecrawl per lo scraping e OCR dedicato) tramite tunnel SSH. Pensato per i flussi di lavoro in produzione.

Il setup guidato dall'AI (`INIT.md`) configurerà la modalità adatta al tuo ambiente.

## Profili di installazione

Il framework si adatta a due forme d'uso. L'installer (`INIT.md`) chiede e sceglie quella giusta.

- **MINIMAL.** Una CLI su una macchina (es. solo Claude Code sul portatile, oppure [OpenCode](https://opencode.ai/go?ref=RK9MPMS1TB) per un setup single-CLI basato su DeepSeek). Ottieni il knowledge vault, le regole del bootstrap, le skill lazy e la disciplina della scrittura memoria tramite una sola porta. Non c'è provisioner da lanciare, nessun doctor da schedulare, niente sync tra macchine. Monti MCP server e skill a mano nella tua CLI. Indicato per chi lavora da solo e vuole governance AgentOps sopra un singolo agente.
- **MULTI.** Due o più CLI e/o due o più macchine. Il provisioner (`agent-sync`), il generatore (`render.py`), il doctor e l'healthcheck entrano in funzione e tengono ogni CLI e ogni macchina allineata alla fonte canonica del vault. Indicato per un setup desktop + portatile, o per girare più CLI in parallelo.

Puoi partire da MINIMAL e passare a MULTI in seguito. I file canonici del vault non cambiano tra i profili.

## Installazione

Non devi compilare i file di configurazione a mano.

1. Clona il repository:
   ```bash
   git clone https://github.com/matteopasseri407/NeXgen-Vault-OL.git ~/KnowledgeVault
   cd ~/KnowledgeVault
   ```
   > Preflight opzionale: `bash install.sh` — controlla i prerequisiti, verifica lo scaffold, rileva le tue CLI e stampa il passo successivo. Non scrive nulla ed è sicuro da ri-lanciare.
2. Apri `INIT.md`.
3. Incolla il contenuto in una **CLI agentica capace di scrivere file** (Claude Code, Codex, OpenCode, Antigravity) aperta in questa cartella — non una chat web (claude.ai / gemini), che non può scrivere file.
4. L'agente ti chiederà quante CLI e macchine hai, il tuo hardware e la modalità di deployment, poi configurerà il vault in automatico.

## Prerequisiti

- Git
- Python 3 con PyYAML (`pip install pyyaml`)
- Node.js (per `npx`, necessario se monti server MCP o skill esterne)
- Opzionale: [OpenCode](https://opencode.ai/go?ref=RK9MPMS1TB) come una delle CLI supportate
- `jq` e `curl` su Linux/Mac (solo per il profilo MULTI, necessari per sync e health)

## Stato per piattaforma

Linux è la piattaforma usata quotidianamente e la più testata. Il supporto Windows è in early preview, ci sto lavorando attivamente: gli script PowerShell del profilo MULTI (`agent-sync.ps1`, `agent-doctor.ps1`) girano, ma il generatore di config MCP (`render.py`) non ha ancora un dialetto Windows, e un paio di percorsi runtime (ad esempio dove Antigravity legge il suo file di istruzioni) sono dedotti per analogia con Linux, non ancora confermati dal vivo. Il profilo MINIMAL è il punto di partenza più sicuro su Windows oggi. macOS segue gli stessi percorsi di codice di Linux ma ha visto meno uso reale.

## Licenza

Vedi `LICENSE`.

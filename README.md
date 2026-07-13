# NeXgen Engine (Alpha)

[![CI](https://github.com/matteopasseri407/NeXgen-Engine/actions/workflows/ci.yml/badge.svg)](https://github.com/matteopasseri407/NeXgen-Engine/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/matteopasseri407/NeXgen-Engine)](https://github.com/matteopasseri407/NeXgen-Engine/releases/latest)
[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue)](LICENSE)

A Git-backed AgentOps control layer for AI coding CLIs — in plain terms, a shared rulebook and memory for AI agent tools like Claude Code, useful for non-coding work (notes, research, career docs) just as much as for software projects. Note: This project is currently in Alpha.

Shared instructions, generated MCP config, drift checks, secrets discipline, and cross-machine agent memory, all as plain files in a Git repo, not a hosted service.

You use Claude Code, Codex, OpenCode, or Antigravity, maybe more than one, maybe on two machines.
Each CLI reads its bootstrap instructions from a different file, keeps its own MCP config, and has no idea what the others are doing.
Change one and the rest drift out of sync, usually without anyone noticing until something breaks.
NeXgen Engine gives them one canonical source and a way to check whether they've drifted from it.

## Who this is for

You run at least one agentic CLI on your own machine and want the actual vault, not a demo of one.
If you run several CLIs, or the same setup across more than one machine, that's where the framework does most of its work: the provisioner and doctor scripts described below exist for that case.
If it's just one CLI on one machine, you still get the knowledge vault and the bootstrap discipline, without needing to run any of the sync tooling.

Evaluating this for more than one person (a couple of colleagues, a small company)? The security and identity model is mono-user today. Read [`docs/team.md`](docs/team.md) and, if you're weighing a shared Cloud-Server backend, [`docs/org-deployment.md`](docs/org-deployment.md) before you adopt it as shared infrastructure. Security posture and how to report an issue are in [`SECURITY.md`](SECURITY.md).

## Demo path

1. Clone the repo and run the preflight: `bash install.sh --check` on Linux/Mac, or `.\install.ps1 -Check` from PowerShell on Windows. It checks prerequisites, verifies the vault scaffold, and lists which agentic CLIs it finds on your machine. It writes nothing.
2. Open `INIT.md` and paste it into a filesystem-capable agent CLI (Claude Code, Codex, OpenCode, Antigravity), not a web chat, which can't write files. The agent interviews you (how many CLIs, how many machines, Local-Only or Cloud-Server) and writes `99-INDEX/USER-PROFILE.md`.
3. The agent mounts the MCP servers and skills for your chosen CLI(s), following the manifests in `03-INFRA/`.
4. If you're on the MULTI profile (2+ CLIs or machines), run `agent-sync apply` to propagate the canonical config, then `agent-doctor` to see the actual compliance check: 30+ live checks against your running CLIs, VPS services, and secrets handling, with a pass, warn, or fail on each line. On Windows the first `apply` also adds the commands' directory to your user PATH — open a new terminal afterwards so `agent-sync`, `agent-doctor`, `vault-groom` and `vault-push` resolve as bare commands.
5. Change something by hand afterward (a stray MCP entry, a config file edited outside the vault) and run `agent-doctor` again. That's the drift check working.

## What this does not do

No UI, no hosted dashboard, no proprietary memory store.
It doesn't compete with a RAG builder or a workflow orchestrator.
It assumes you already have opinions about which agents and tools you want, and gives them a shared, auditable floor to run on.

NeXgen's public-engine safety gates are maintainer tooling, not an end-user chore. Normal users push only their private vault data. Checks such as `engine-push`, public-repo leak gates, and disabled direct push on an engine development clone matter only for people publishing changes to this GitHub repository.

**What NeXgen does not do:** NeXgen governs configuration — one canonical source, generated derivatives, drift detection, single-door writes. It does **not** sit between an agent and its tools at runtime: `agent-doctor` cannot block a call made with hallucinated but valid-looking arguments. That boundary is enforced by your CLI harness (permission modes, user approval prompts) and by server-side validation in the MCP servers themselves (e.g., the `expected_hash` lock in `vault-library`).

## Core concepts

- **Infrastructure as Code for AI.** Manifest files define tools, permissions, and agent behaviors. A unified Python script (`agent_sync.py`) generates the correct configuration for different CLIs.
- **Git-backed memory.** The agents read and write Markdown files. Every change is version-controlled, diffable, and easy to revert.
- **Vault grooming (optional, on-demand).** `vault-groom.sh`/`.ps1` runs an LLM over a grooming playbook to flag stale, duplicate, or dead notes. A bare run (or `preview`) is always read-only. `vault-groom apply` is the guarded lane: it proposes a tranche, shows it in full, and only after you type `yes` does the write pass run — inside a disposable clone of the vault with no remote configured, so it physically cannot push. A mechanical audit then compares what was actually committed against the approved tranche, in both directions, and only a fully clean run gets promoted (fast-forwarded) into your real vault; anything else stays quarantined in the clone, with your vault untouched. Works with whichever of `claude`, `codex`, or `agy` you already have (`GROOM_RUNNER`). An optional n8n workflow only reminds you it's due every 14 days — the grooming pass itself is never scheduled or run unattended.
- **Deterministic AI Council (Alpha).** A local orchestrator (`council.py`) that coordinates multiple models for brainstorming and relay tasks. It uses explicit Python code to pass control, rather than relying on an LLM to manage the rules.
- **Drift detection.** In MULTI profile, the `agent-doctor` script runs 30+ read-only checks against your CLIs' live configuration, vault wiring, skills, and secrets handling, reporting pass/warn/fail per line (non-zero exit code on failures). It detects drift and misconfiguration; it does not sit in the execution path. In MINIMAL, there is no doctor: a single CLI on a single machine is verified visually.
- **Cross-platform consistency (optional).** In MULTI profile, the system forces agents to behave identically across different machines (e.g., a Windows workstation and a Linux laptop) through a provisioner. In MINIMAL, there is only one machine, so the provisioner is a no-op and is not installed.

## Architecture: The Three Planes

NeXgen Engine separates operations into three distinct planes:

1. **Behavior:** A single operating policy (`AGENTS.md`) linked into every runtime.
2. **Configuration:** An abstract MCP manifest compiled into each CLI's specific dialect by a generator script.
3. **Memory:** A plain-Markdown vault, written through serialized paths. 

Writes go through one door per kind of thing. Knowledge notes are written only through a memory tool server that serializes with a lock and an expected-hash check, preventing agents from overwriting each other's work.

**Skills stay lazy by design.** Tool awareness and policy remain in the
bootstrap and MCP manifest. Optional task playbooks live outside eager
discovery roots and are opened only when needed. See
[`docs/lazy-skills.md`](docs/lazy-skills.md).

## Shared Tools via MCP (Modular & Free-Tier Ready)

Agents share infrastructure rather than reinventing it. A few services run once, in an environment you deploy and own (not a service this project or its author operates for you), and every agent reaches them over the Model Context Protocol (MCP):

> **Note:** These specific tools are completely interchangeable. They were selected because they run comfortably and at zero cost on an **Oracle Cloud Always Free VPS** (4 ARM Ampere cores, 24GB RAM, 200GB SSD) — a tier anyone can provision for themselves. You can easily swap them for enterprise equivalents.

- **Semantic Search (bring-your-own):** the `vault-library` MCP contract (`semantic_search`, see `manifest.yaml`) is ready to call, and the retrieval governance in `AGENTS.md` routes to it. Unlike the three tools below, **no deploy code for the search backend itself ships in this repo** — `03-INFRA/deploy/` has no `semantic-search/` folder. Build and host your own service behind that contract (a self-hosted retrieval layer over static embeddings + BM25 is a proven shape for it) if you want this lane to actually answer; without one, agents fall back to lexical search per the governance doc.
- **Web Scraping:** A self-hosted Firecrawl instance you deploy (included in `03-INFRA/deploy/firecrawl/`) serves as the default read-only lane.
- **Local OCR:** A self-hosted OCR service you deploy (included in `03-INFRA/deploy/ocr/`) extracts text from screenshots, logs, and scanned documents locally.
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
2. **Cloud-Server.** Connects to a self-hosted stack (like n8n for orchestration, Firecrawl for scraping, and dedicated OCR) deployed in **your own private environment** (VPS or local server) over an SSH tunnel. You maintain full ownership of your data; NeXgen does not provide or host these services for you.

The AI-guided setup (`INIT.md`) configures the correct mode for your environment.

## Installation profiles

The framework fits two shapes of usage. The installer (`INIT.md`) asks and picks the right one.

- **MINIMAL.** One CLI on one machine (e.g., only Claude Code on your laptop, or [OpenCode](https://opencode.ai) for a DeepSeek-based single-CLI setup). You get the knowledge vault, the bootstrap rules, lazy skills, and the discipline of writing memory through one door. There is no provisioner to run, no doctor to schedule, no cross-machine sync. Mount the MCP servers and skills you want directly in your CLI by hand. Best for solo users who just want AgentOps governance on top of a single agent.
- **MULTI.** Two or more CLIs and/or two or more machines. The unified Python provisioner (`agent_sync.py`), the doctor, and the healthcheck come online and keep every CLI and machine aligned to the canonical source in the vault. Best for a workstation + laptop setup, or for running multiple CLIs side by side.

MULTI propagation is a locked, fail-closed transaction. The pull must prove the
data fresh against one authoritative remote before runtime files are regenerated;
publishing is always a separate command. See
[`docs/sync-contract.md`](docs/sync-contract.md).

You can start MINIMAL and switch to MULTI later. The canonical files in the vault do not change between profiles.

## Installation

You don't need to fill out configuration files manually.

1. Clone the repository:
   ```bash
   git clone https://github.com/matteopasseri407/NeXgen-Engine.git ~/KnowledgeVault
   cd ~/KnowledgeVault
   ```
   > Optional preflight: `bash install.sh` checks prerequisites, verifies the scaffold, detects your CLIs, and prints the next step. It writes nothing and is safe to re-run.
2. Open `INIT.md`.
3. Paste its contents into a **filesystem-capable agent CLI** (Claude Code, Codex, OpenCode, Antigravity) opened in this folder, not a plain web chat (claude.ai / gemini), which cannot write files.
4. The agent will ask how many CLIs and machines you have, your hardware, and your deployment mode, then configure the vault automatically.

Prefer fewer questions and more autonomy? `AI-INSTALLER.md` is the same install with minimal back-and-forth: paste it instead of `INIT.md` and the agent runs the steps itself rather than interviewing you one question at a time.

## Prerequisites

- Git
- Python 3.11+ with PyYAML (`pip install pyyaml`), or Python 3.10 with `tomli` too (`pip install pyyaml tomli`)
- Node.js (for `npx`, needed if you mount MCP servers or external skills)
- Optional: [OpenCode](https://opencode.ai) as one of the supported CLIs
- `jq` and `curl` on Linux/Mac (only needed for the MULTI profile sync and health scripts)

## Platform status

**Linux: released.** Linux is the daily-driven, most-tested platform, and this is the cut to run there: the whole engine — provisioner, doctor, gardener, council, sync — is exercised end to end on Fedora and green on CI. macOS follows the same POSIX code paths but has seen less real-world use.

**Why is this still Alpha?** Cross-platform support and the core orchestrators are still settling:
- **Windows: software-complete, not yet physically verified.** The provisioner (`agent_sync.py`), the MCP config generator (`render.py`, via a per-server `windows:` override block in the manifest), and the PowerShell launchers all have a Windows dialect, and CI runs the *full* pytest suite — including the pwsh gardener tests — on `windows-latest` (job `engine-tests-windows`) on every push, now genuinely green after a round of real portability fixes (console UTF-8, plan-record byte-parity, prompt delivery through the launcher, and file locking). That proves the shared code paths on a CI runner, not a physical install: a couple of runtime paths (e.g. the Antigravity instructions file) are still inferred by analogy with Linux, and the vendor adapters still want a live cross-platform pass. Treat Windows as preview until that physical verification lands; the MINIMAL profile is the safer starting point there today.
- **AI Council:** The deterministic orchestrator (`council.py`) supports `opencode`, `agy`, `codex`, `claude`, and `ollama` seats. Its optional routing adapter proposes exact locally verified models and efforts, with declared fallbacks, without letting an external workflow rewrite private cross-machine data or auto-invoke a seat. A human explicitly chooses the seat count and models.

## License

PolyForm Noncommercial License 1.0.0. Free for any noncommercial use, including reading, running, forking, and modifying it. See `LICENSE` for the full text. Any commercial use, of the original software or a derivative, needs a separate license from the author: see `COMMERCIAL.md`.

## Support

This project is free to use. Some optional links (like the OpenCode one above) are referral links that fund maintenance at no extra cost to you: see `SUPPORT.md` for the one place they're declared.

---

# NeXgen Engine, versione italiana, Alpha

NeXgen Engine è un control layer AgentOps basato su Git per le CLI agentiche.
In pratica, raccoglie in un unico repository le regole condivise e la memoria di lavoro per strumenti come Claude Code.
Può essere usato per programmare, prendere note, fare ricerca o preparare documenti professionali.
Il progetto è ancora in fase Alpha.

Le istruzioni, la configurazione MCP, i controlli di drift, la gestione dei segreti e la memoria condivisa sono tutti file di testo dentro un repository Git.
Non c'è un servizio cloud proprietario da attivare o da cui dipendere.

Se usi Claude Code, Codex, OpenCode o Antigravity, ogni CLI ha il proprio file di bootstrap e la propria configurazione MCP.
Di default, una CLI non sa cosa è stato cambiato nelle altre.
NeXgen mette tutto questo sotto una fonte canonica e controlla quando le configurazioni si sono allontanate da essa.

## A chi serve

Serve a chi usa almeno una CLI agentica sul proprio computer e vuole un vault vero, non una demo usa e getta.
Il vantaggio maggiore arriva quando usi più CLI oppure lo stesso setup su più macchine, perché il provisioner e gli script di controllo tengono tutto allineato.
Con una sola CLI su una sola macchina puoi comunque usare il knowledge vault e le regole di bootstrap, senza installare gli strumenti di sincronizzazione.

Se lo stai valutando per più persone, tieni presente che il modello di identità e sicurezza è ancora pensato per un solo utente.
Prima di usarlo come infrastruttura condivisa, leggi [`docs/team.md`](docs/team.md) e, se stai pensando a un backend Cloud-Server comune, [`docs/org-deployment.md`](docs/org-deployment.md).
La postura di sicurezza e le istruzioni per segnalare problemi sono in [`SECURITY.md`](SECURITY.md).

## Percorso demo

1. Clona il repository ed esegui il preflight: `bash install.sh --check` su Linux o macOS, oppure `.\install.ps1 -Check` da PowerShell su Windows.
   Il comando controlla i prerequisiti, verifica la struttura del vault e mostra quali CLI agentiche trova.
   Non modifica nulla.
2. Apri `INIT.md` e incollalo in una CLI agentica che possa scrivere file, come Claude Code, Codex, OpenCode o Antigravity.
   Una chat web non basta, perché non può modificare il repository.
   L'agente ti chiede quante CLI e quante macchine vuoi usare, oltre alla modalità Local-Only o Cloud-Server, poi compila `99-INDEX/USER-PROFILE.md`.
3. L'agente monta i server MCP e le skill per le CLI scelte, usando i manifest presenti in `03-INFRA/`.
4. Se usi il profilo MULTI, cioè almeno due CLI o due macchine, esegui `agent-sync apply` per propagare la configurazione canonica.
   Poi esegui `agent-doctor` per controllare lo stato reale, con oltre 30 verifiche su CLI, servizi VPS e gestione dei segreti.
   Ogni verifica restituisce `pass`, `warn` o `fail`.
   Su Windows, il primo `apply` aggiunge anche la cartella dei comandi al PATH dell'utente, quindi dopo devi aprire un nuovo terminale per usare direttamente `agent-sync`, `agent-doctor`, `vault-groom` e `vault-push`.
5. Modifica qualcosa fuori dal vault, per esempio una voce MCP o un file di configurazione, poi esegui di nuovo `agent-doctor`.
   Vedrai il controllo del drift in azione.

## Cosa fa e cosa non fa

NeXgen non è un'applicazione con interfaccia grafica, non offre una dashboard online e non include un motore di memoria proprietario.
Non è un builder RAG e non è un orchestratore di workflow.
Parte dal presupposto che tu abbia già scelto gli agenti e gli strumenti da usare, poi fornisce loro una base comune, versionata e verificabile.

NeXgen governa la configurazione, usando una fonte canonica, file derivati generati automaticamente, controlli di drift e una porta separata per ogni tipo di scrittura.
Non si mette però tra l'agente e i suoi tool mentre lavorano.
Per esempio, `agent-doctor` non può bloccare una chiamata che contiene argomenti plausibili ma sbagliati.
I controlli a runtime spettano all'harness della CLI, con i suoi permessi e le richieste di conferma, e ai server MCP, che validano le richieste lato server, per esempio con il lock `expected_hash` di `vault-library`.

## Concetti base

- **Infrastruttura come codice per gli agenti.** I manifest descrivono tool, permessi e regole di comportamento.
  Lo script Python unificato `agent_sync.py` genera poi il file di configurazione corretto per ogni CLI.
- **Memoria versionata in Git.** Gli agenti leggono e scrivono file Markdown.
  Ogni modifica entra nella storia del repository, si può controllare con un diff e si può annullare.
- **Grooming del vault, opzionale e manuale.** `vault-groom.sh` e `vault-groom.ps1` usano un playbook e un LLM per trovare note obsolete, duplicate o scollegate.
  L'esecuzione semplice, così come `preview`, è sempre in sola lettura.
  Con `vault-groom apply`, lo strumento propone una tranche di modifiche, la mostra per intero e avvia la scrittura solo dopo che hai digitato `yes`.
  La scrittura avviene in un clone usa e getta del vault, senza remote configurato, quindi da quel clone non è possibile fare push.
  Un audit confronta il risultato con la tranche approvata e promuove il lavoro nel vault reale solo se tutto torna.
  Se qualcosa non torna, il clone resta in quarantena e il vault originale non viene toccato.
  Puoi usare la CLI che hai già tra `claude`, `codex` e `agy`, tramite `GROOM_RUNNER`.
  Un workflow n8n opzionale ti ricorda ogni 14 giorni di eseguire il grooming, ma non avvia mai il lavoro al posto tuo.
- **Consiglio AI deterministico, in Alpha.** `council.py` è un orchestratore locale per coordinare più modelli in attività di brainstorming o relay.
  Le regole di passaggio sono scritte in Python, non affidate a un altro LLM.
- **Controllo del drift.** Nel profilo MULTI, `agent-doctor` esegue oltre 30 verifiche in sola lettura sulla configurazione delle CLI, sul collegamento al vault, sulle skill e sulla gestione dei segreti.
  Per ogni voce mostra `pass`, `warn` o `fail` e restituisce un exit code diverso da zero se trova errori.
  Rileva configurazioni fuori posto, ma non blocca l'esecuzione degli agenti.
  Nel profilo MINIMAL non c'è un doctor, perché una sola CLI su una sola macchina si controlla direttamente.
- **Coerenza tra macchine, opzionale.** Nel profilo MULTI il provisioner mantiene lo stesso comportamento su macchine diverse, per esempio una workstation Windows e un portatile Linux.
  Nel profilo MINIMAL, con una sola macchina, il provisioner non serve e non viene installato.

## Architettura: i tre piani

NeXgen separa il sistema in tre piani:

1. **Comportamento.** Una sola policy operativa, `AGENTS.md`, collegata a ogni ambiente in cui gira una CLI.
2. **Configurazione.** Un manifest MCP astratto, trasformato dal generatore nel formato richiesto da ciascuna CLI.
3. **Memoria.** Un vault in Markdown, con le scritture serializzate per evitare conflitti.

Ogni tipo di scrittura passa dalla propria porta.
Le note, per esempio, vengono scritte solo tramite un server MCP che usa un lock e controlla l'hash atteso, così un agente non può sovrascrivere per errore il lavoro di un altro.

**Le skill vengono caricate solo quando servono.** Le regole e la conoscenza dei tool restano nel bootstrap e nel manifest MCP.
I playbook opzionali vivono fuori dalle cartelle di discovery automatica e vengono aperti solo per i task che ne hanno bisogno.
Vedi [`docs/lazy-skills.md`](docs/lazy-skills.md).

## Tool condivisi tramite MCP

Gli agenti possono usare gli stessi servizi invece di configurarli da capo ogni volta.
Sono servizi che installi e gestisci tu in un ambiente di tua proprietà, non servizi offerti o amministrati dall'autore di NeXgen.
Gli agenti li raggiungono tramite il Model Context Protocol, MCP.

> **Nota:** questi servizi sono intercambiabili.
> Sono stati scelti perché possono girare a costo zero su una **VPS Oracle Cloud Always Free** con 4 core ARM Ampere, 24 GB di RAM e 200 GB di SSD.
> Puoi sostituirli con equivalenti Enterprise o con servizi self-hosted diversi.

- **Ricerca semantica, da configurare a parte.** Il contratto MCP `vault-library` espone già `semantic_search`, il manifest `manifest.yaml` lo dichiara e la governance di retrieval in `AGENTS.md` sa come usarlo.
  Il repository, però, non contiene il backend di ricerca né il suo codice di deploy: in `03-INFRA/deploy/` non c'è una cartella `semantic-search/`.
  Se vuoi usare questa funzione, devi costruire e gestire un servizio compatibile con quel contratto.
  In sua assenza, gli agenti ricadono sulla ricerca lessicale prevista dalla governance.
- **Web scraping.** Puoi installare una tua istanza self-hosted di Firecrawl, con i file di deploy in `03-INFRA/deploy/firecrawl/`.
  È la corsia predefinita per le letture web in sola lettura.
- **OCR locale.** Puoi installare un servizio OCR self-hosted, con i file in `03-INFRA/deploy/ocr/`, per estrarre testo da screenshot, log e documenti scansionati senza inviarli a un servizio esterno.
- **Browser visibile.** Per form, login e controlli interattivi, gli agenti si collegano a una finestra Chrome reale tramite il protocollo DevTools.
  Non devono eseguire browser headless alle tue spalle.

## Cosa non abbiamo costruito

Non abbiamo creato un motore di memoria proprietario.
Markdown, Git e un semplice server MCP bastano a fornire una memoria durevole, versionata e leggibile sia dagli umani sia dagli agenti.
Non troverai un sistema di negoziazione autonoma tra agenti, un pianificatore Swarm A*, CRDT o un secondo database.
Il lavoro è concentrato sul livello che sta sopra lo storage, cioè sulla governance operativa e sui controlli di sicurezza.

## Contenuto

| Directory | Scopo |
|---|---|
| `03-INFRA/` | Il motore, con le regole base (`AGENTS.md`), i manifest dei server MCP e gli script di validazione (`agent-sync`, `agent-doctor`). |
| `99-INDEX/` | Il livello di identità, con le informazioni su hardware, sistema operativo e contesto di deployment (`USER-PROFILE.md`). |
| `01-NOTES/` | Lo spazio di lavoro per la documentazione. |
| `02-PROJECTS/` | Il tracciamento dei progetti e delle attività. |
| `04-NOW/` | Le priorità attive, per evitare che gli agenti si disperdano in aree non pertinenti. |

## Modalità di deployment

1. **Local-Only.** Tutto gira sulla tua macchina, usando i tool nativi delle CLI e, se vuoi, modelli locali.
   È la modalità adatta per i test e per un setup personale.
2. **Cloud-Server.** Il vault si collega a uno stack remoto, per esempio n8n per l'orchestrazione, Firecrawl per lo scraping e un servizio OCR dedicato.
   Lo stack gira in un ambiente privato che installi e amministri tu, come una VPS o un server locale, e viene raggiunto tramite tunnel SSH.
   NeXgen non fornisce né ospita questi servizi, quindi i dati restano sotto il tuo controllo.

Il setup guidato dall'AI in `INIT.md` configura la modalità più adatta al tuo ambiente.

## Profili di installazione

Il setup guidato da `INIT.md` ti chiede quale dei due profili descrive meglio il tuo caso.

- **MINIMAL.** Una sola CLI su una sola macchina, per esempio Claude Code sul portatile oppure [OpenCode](https://opencode.ai) in un setup basato su DeepSeek.
  Ottieni il knowledge vault, le regole di bootstrap, le skill caricate quando servono e la scrittura della memoria attraverso una sola porta.
  Non devi avviare un provisioner, programmare un doctor o sincronizzare più macchine.
  Monti manualmente nella CLI i server MCP e le skill che vuoi usare.
  È il profilo giusto per chi lavora da solo e vuole una governance AgentOps sopra una singola CLI.
- **MULTI.** Due o più CLI, oppure due o più macchine.
  Il provisioner Python `agent_sync.py`, il doctor e l'healthcheck mantengono ogni ambiente allineato alla fonte canonica nel vault.
  È il profilo adatto a una configurazione desktop più portatile o a chi usa più CLI in parallelo.

Nel profilo MULTI, la propagazione avviene come una transazione con lock e si interrompe in modo sicuro se qualcosa non torna.
Prima di rigenerare i file runtime, il pull deve dimostrare che i dati arrivano dal remote autorevole e sono aggiornati.
La pubblicazione è sempre un comando separato.
Il contratto completo è in [`docs/sync-contract.md`](docs/sync-contract.md).

Puoi iniziare con MINIMAL e passare a MULTI in seguito.
I file canonici del vault restano gli stessi in entrambi i profili.

## Installazione

Non devi preparare a mano i file di configurazione.

1. Clona il repository:
   ```bash
   git clone https://github.com/matteopasseri407/NeXgen-Engine.git ~/KnowledgeVault
   cd ~/KnowledgeVault
   ```
   > Preflight facoltativo: `bash install.sh` controlla i prerequisiti, verifica la struttura del vault, rileva le CLI installate e mostra il passo successivo.
   > Non scrive nulla ed è sicuro da eseguire più volte.
2. Apri `INIT.md`.
3. Incolla il contenuto in una **CLI agentica capace di modificare file**, come Claude Code, Codex, OpenCode o Antigravity, aperta nella cartella del repository.
   Non usare una chat web come claude.ai o gemini, perché non può scrivere i file del progetto.
4. L'agente ti chiederà quante CLI e quante macchine vuoi usare, quali sono le caratteristiche del tuo computer e quale modalità di deployment preferisci.
   Poi configurerà il vault in automatico.

Se vuoi ridurre al minimo le domande, usa `AI-INSTALLER.md` al posto di `INIT.md`.
L'agente eseguirà la stessa procedura in autonomia, chiedendo solo le informazioni indispensabili.

## Prerequisiti

- Git.
- Python 3.11 o superiore con PyYAML, installabile con `pip install pyyaml`.
- Python 3.10 con PyYAML e `tomli`, installabili con `pip install pyyaml tomli`.
- Node.js, necessario per `npx` se vuoi montare server MCP o skill esterne.
- [OpenCode](https://opencode.ai), opzionale, come una delle CLI supportate.
- `jq` e `curl` su Linux o macOS, necessari solo per il sync e gli healthcheck del profilo MULTI.

## Stato per piattaforma

**Linux: rilasciato.** È la piattaforma usata ogni giorno e quella su cui il progetto è stato provato di più.
In questa versione, provisioner, doctor, grooming, council e sync sono stati verificati end to end su Fedora e passano la CI.
macOS segue gli stessi percorsi POSIX, ma ha ricevuto meno verifiche nell'uso reale.

**Perché il progetto è ancora in Alpha?** Il supporto multipiattaforma e gli orchestratori principali non sono ancora considerati definitivi.
- **Windows: completo a livello software, ma non ancora verificato su una macchina reale.** `agent_sync.py`, il generatore della configurazione MCP `render.py`, tramite un blocco di override `windows:` per ogni server nel manifest, e i launcher PowerShell includono un dialetto Windows.
  La CI esegue l'intera suite pytest su `windows-latest`, compresi i test PowerShell del grooming, nel job `engine-tests-windows` a ogni push.
  I test sono verdi dopo i fix di portabilità per encoding UTF-8, byte parity dei plan record, passaggio dei prompt attraverso i launcher e file locking.
  Questo dimostra che il codice condiviso funziona su un runner CI, non che l'installazione sia stata provata su un PC reale.
  Alcuni percorsi runtime, come il file di istruzioni di Antigravity, sono ancora dedotti per analogia con Linux, e gli adapter dei vendor richiedono una verifica multipiattaforma dal vivo.
  Considera quindi Windows una preview e parti dal profilo MINIMAL.
- **Consiglio AI.** L'orchestratore deterministico `council.py` supporta i seat `opencode`, `agy`, `codex`, `claude` e `ollama`.
  Il routing opzionale propone modelli ed effort verificati localmente, con fallback espliciti.
  Non permette a un workflow esterno di riscrivere dati privati tra più macchine o di avviare automaticamente un seat.
  La scelta del numero di seat e dei modelli resta sempre esplicita e umana.
  I test automatici coprono il flusso dei quattro mode.

## Licenza

PolyForm Noncommercial License 1.0.0.
Il progetto è gratuito per qualsiasi uso non commerciale, compresi lettura, esecuzione, fork e modifiche.
Il testo completo è in `LICENSE`.
Qualsiasi uso commerciale del software originale o di un suo derivato richiede una licenza separata dell'autore, come spiegato in `COMMERCIAL.md`.

## Supporto

Il progetto è gratuito.
Alcuni link opzionali, incluso quello di OpenCode, sono referral link che aiutano a finanziare la manutenzione senza costi aggiuntivi per te.
Sono dichiarati tutti in `SUPPORT.md`.

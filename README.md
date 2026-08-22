# NeXgen Engine

<p align="center">
  <img src="assets/nexgen-architecture-banner.png" alt="NeXgen Engine — AI Operating Layer" width="100%">
</p>

<p align="center">
  <a href="https://github.com/matteopasseri407/NeXgen-Engine/actions/workflows/ci.yml"><img src="https://github.com/matteopasseri407/NeXgen-Engine/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/matteopasseri407/NeXgen-Engine/releases/latest"><img src="https://img.shields.io/github/v/release/matteopasseri407/NeXgen-Engine?display_name=tag&label=latest%20version" alt="Latest version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-00E5B8?logo=python&logoColor=white" alt="Python 3.10+">
</p>

[Leggi in italiano ↓](#nexgen-engine-versione-italiana)

**One Canonical Source. Any Agent. Always in Sync.**

NeXgen Engine is a deterministic AI Operating Layer that unifies instructions, tool configuration, secrets, and version-controlled memory across Claude Code, Codex, OpenCode, and Antigravity.

Instead of letting individual agent CLI configurations diverge across machines, NeXgen maintains a single source of truth in Git, compiled into each assistant's native format and verified by automated diagnostics.

---

## The Three Planes

NeXgen structures agent operations into three decoupled planes:

1. **Behavior:** Universal operating policies, prompts, and invariant guardrails defined in `AGENTS.md` and symlinked into every runtime.
2. **Configuration:** Abstract MCP manifests and skills compiled deterministically into each CLI's native configuration format via `nexgen sync`.
3. **Memory:** Plain Markdown KnowledgeVault with compare-and-swap (CAS) concurrency locking, per-section updates (`update_section`), and automatic Git versioning.

```
                         ┌──────────────┐
                         │  AGENTS.md   │ ──► [ BEHAVIOR ]
                         └──────┬───────┘
                                │
   ┌────────────────────────────┼────────────────────────────┐
   │                            ▼                            │
   │                    ┌───────────────┐                    │
   │                    │ neXgen Engine │                    │
   │                    └───────┬───────┘                    │
   │                            │                            │
   ▼                            ▼                            ▼
[ CONFIGURATION ]         [ SECRETS ]                   [ MEMORY ]
MCP Manifests / Skills    Age Multi-Recipient Store     KnowledgeVault
Claude · Codex ·          Zero-Passphrase (0600)        CAS Locked Git Notes
OpenCode · Antigravity    Per-Host OAuth Slots          Link Hygiene Map
```

---

## Key Features in v2.0.0

* **Unified Python Core (`nexgen_core`):** Pure Python implementation running natively across Linux and Windows with 400+ unit tests, eliminating shell script divergence.
* **Deterministic Modular Layer:** 8-module catalog (`memory`, `semantic-rag`, `firecrawl`, `ocr`, `n8n`, `browser`, `council`, `sync`) managed deterministically with `nexgen modules list` and `nexgen modules set`.
* **Zero-Passphrase Secrets Store:** Asymmetric `age` encryption (`99-SECRETS/secrets.yaml.age`) using machine-local hardware keys (`0600`), isolated per-host OAuth refresh token slots, and materialized `secrets.env` for shells and systemd services.
* **Visual CLI & Operator Shell:** Built-in `nexgen info` visual dashboard and standalone `nexgen shell` interactive REPL with selectable menu actions (`[1-7]`), enabling complete human management without opening an AI assistant.
* **Comprehensive Multi-Runtime Alignment:** First-class support for Claude Code, Codex, OpenCode, and Antigravity (including Council seat integration).
* **Fail-Closed Diagnostics (`nexgen doctor`):** 33+ automated sanity checks validating git alignment, manifest reachability, link hygiene, token presence, and permission boundaries.

---

## Quick Start

### 1. Bootstrap the Engine
Clone the repository to initialize your KnowledgeVault:
```bash
git clone https://github.com/matteopasseri407/NeXgen-Engine.git ~/KnowledgeVault
cd ~/KnowledgeVault
bash install.sh --check
```
*(On Windows PowerShell: `.\install.ps1 -Check`)*

### 2. Configure Your Environment
Open `INIT.md` and paste its contents into your preferred agent CLI (Claude Code, Codex, OpenCode, or Antigravity). The agent will guide you through profile selection and module setup.

### 3. Align and Verify
Run the engine synchronizer and diagnostics:
```bash
nexgen sync
nexgen doctor
```

### 4. Interactive Operator Shell
Launch the human dashboard and management shell anytime:
```bash
nexgen info
nexgen shell
```

---

## Platform Support

<!-- platform-status:start -->

| System | Status | On what evidence |
|---|---|---|
| Linux | **released** | the platform this is developed and used on daily; the full cycle (install, alignment, doctor, grooming, council, update) runs here and in CI |
| Windows | **released** | verified on real hardware and in CI; full native Python execution, dual launchers, and complete CLI alignment |
| macOS | **untested** | shares the POSIX paths with Linux and should work, but nobody has run it end to end; treat a failure here as expected, and reporting it as useful |

| Assistant | Status | What is covered |
|---|---|---|
| Claude Code | **complete** | instructions, MCP connectors, skills, guardrails |
| Codex | **complete** | instructions, MCP connectors, skills |
| OpenCode | **complete** | instructions, MCP connectors, skills |
| Antigravity | **complete** | instructions, MCP connectors, skills, and a Council seat; the seat was unblocked on 2026-08-22 with a stateless invocation (agy --model ... --disable-slash-commands --new-project --sandbox -p <prompt>) verified live with a nonce prompt |

<!-- platform-status:end -->

---

## Architecture Boundaries

* **No Lock-In:** All memories and configuration are stored as human-readable Markdown and YAML in Git.
* **Deterministic Write Paths:** Knowledge notes are modified exclusively via CAS hash verification to prevent race conditions.
* **Non-Invasive Execution:** The engine manages configuration as code above runtime execution; it does not intercept real-time model token streams.

---

## License

PolyForm Noncommercial License 1.0.0. Free for noncommercial use, modification, and self-hosted deployments. See `LICENSE` for details.

---

# NeXgen Engine, versione italiana

<p align="center">
  <img src="assets/nexgen-architecture-banner.png" alt="NeXgen Engine — AI Operating Layer" width="100%">
</p>

**Una sola sorgente canonica. Qualsiasi agente. Sempre allineati.**

NeXgen Engine è un layer operativo per agenti AI che unifica istruzioni, configurazione degli strumenti, gestione dei segreti e memoria di lavoro versionata tra Claude Code, Codex, OpenCode e Antigravity.

Invece di lasciare che le configurazioni delle varie CLI divergano tra computer diversi, NeXgen mantiene un unico repository Git come sorgente di verità, compilato nei formati nativi di ogni assistente e validato da controlli diagnostici automatici.

---

## I Tre Piani Architetturali

NeXgen organizza il lavoro degli agenti in tre piani separati:

1. **Comportamento (Behavior):** Regole operative universali, prompt e guardrail immutabili definiti in `AGENTS.md` e collegati a ogni runtime.
2. **Configurazione (Configuration):** Manifest astratti di connettori MCP e skill, compilati in modo deterministico nei formati delle varie CLI tramite `nexgen sync`.
3. **Memoria (Memory):** KnowledgeVault in puro Markdown con blocco atomico compare-and-swap (CAS), aggiornamenti per singola sezione (`update_section`) e cronologia Git completa.

---

## Principali Novità della Versione 2.0.0

* **Motore Unificato in Python (`nexgen_core`):** Esecuzione nativa multipiattaforma su Linux e Windows con oltre 400 test automatici, eliminando la duplicazione degli script di shell.
* **Layer Modulare Deterministico:** Catalogo di 8 moduli (`memory`, `semantic-rag`, `firecrawl`, `ocr`, `n8n`, `browser`, `council`, `sync`) gestito con i comandi `nexgen modules list` e `nexgen modules set`.
* **Deposito Segreti Age a Zero Passphrase:** Crittografia asimmetrica moderna (`99-SECRETS/secrets.yaml.age`) con chiavi hardware locali a permessi `0600`, slot OAuth per-host isolati contro i conflitti di rotazione dei token e file `secrets.env` per shell e servizi systemd.
* **Dashboard Grafica e Shell Operatore:** Comandi `nexgen info` per il riepilogo visivo dello stato e `nexgen shell` per una REPL interattiva a menu numerato (`[1-7]`), utilizzabile da un operatore umano senza aprire alcuna CLI di AI.
* **Supporto Completo per 4 Runtime:** Allineamento nativo per Claude Code, Codex, OpenCode e Antigravity (incluso il ruolo di seggio nel Consiglio AI).
* **Diagnostica Continua (`nexgen doctor`):** Batteria di oltre 33 controlli automatici su integrità Git, raggiungibilità dei server MCP, igiene dei collegamenti e permessi di sicurezza.

---

## Guida Rapida

### 1. Inizializzazione
Clona il repository per creare la struttura del KnowledgeVault:
```bash
git clone https://github.com/matteopasseri407/NeXgen-Engine.git ~/KnowledgeVault
cd ~/KnowledgeVault
bash install.sh --check
```
*(Su Windows PowerShell: `.\install.ps1 -Check`)*

### 2. Configurazione Guidata
Apri `INIT.md` e incolla il testo nella tua CLI preferita (Claude Code, Codex, OpenCode o Antigravity). L'agente configurerà il profilo e i moduli desiderati.

### 3. Allineamento e Verifica
Sincronizza le configurazioni e controlla lo stato:
```bash
nexgen sync
nexgen doctor
```

### 4. Gestione da Terminale
Apri la schermata di stato o la shell interattiva:
```bash
nexgen info
nexgen shell
```

---

## Licenza

PolyForm Noncommercial License 1.0.0. Gratuito per qualsiasi uso non commerciale, studio e deployment self-hosted. Consulta il file `LICENSE` per il testo completo.

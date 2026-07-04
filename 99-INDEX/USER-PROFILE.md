# Profilo Utente e Host Awareness

> **NOTA PER L'AGENTE INSTALLATORE**: Sostituisci i placeholder `[BRACKETS]` con i dati reali raccolti durante l'intervista in `INIT.md`. Questo file è la "mappa" della macchina dell'utente.

Questo file contiene il contesto personale e le specificità hardware dell'utente. Gli agenti devono leggerlo in fase di inizializzazione per mappare i concetti astratti del framework sulla macchina reale.

## Profilo di installazione

- **profile**: `[MINIMAL | MULTI]` — MINIMAL = 1 CLI su 1 macchina. MULTI = 2+ CLI e/o 2+ macchine, usa `agent-sync` per propagare. La maggior parte delle regole "single source / propagate to all / cross-platform" in `AGENTS.md` è condizionale al `profile: MULTI`.
- **clis**: `[elenco CLI installate, es. claude-code | codex | opencode | antigravity]`
- **machines**: `[elenco macchine, es. primary (questa) | secondary (opzionale)]`
- **sync_method**: `[manual | agent-sync]` — in MINIMAL basta installare la CLI e montare MCP/skill a mano; in MULTI usa il provisioner `agent-sync` per allineare ogni CLI e macchina alla fonte canonica.

## Host Awareness

- **Workstation Principale**: `[INSERIRE OS — es. Windows/Mac/Linux]`, `[INSERIRE SPECIFICHE — es. M2 Max, RTX 4090, 32GB RAM]`. `[INSERIRE NOTE — es. Usala per modelli locali / Non adatta a modelli locali]`.
- **Dispositivo Secondario (Opzionale)**: `[INSERIRE SPECIFICHE SECONDO DEVICE, o Rimuovere se non presente]`.

## Knowledge Vault

- **Workstation Principale**: `[INSERIRE PATH ASSOLUTO — es. /home/utente/KnowledgeVault o C:\Users\utente\KnowledgeVault]`
- **Git remoto (opzionale)**: `[INSERIRE URL DEL TUO FORK, o Rimuovere se non versioni il vault su remoto]`

## Architettura: Local-Only o Cloud-Server

- **Modalità**: `[LOCAL-ONLY] oppure [CLOUD-SERVER]`

### Se LOCAL-ONLY

- Tutto gira sulla macchina locale. Nessun VPS.
- Web search → tool nativo della CLI (firecrawl assente).
- OCR → vision del modello (OCR self-hosted assente).
- Automazioni remote → non disponibili (n8n remoto assente).
- Variabile d'ambiente: `KNOWLEDGE_VAULT_REMOTE="local"`

### Se CLOUD-SERVER

- **Remote backend (VPS)**:
  - SSH alias: `[INSERIRE ALIAS SSH]`
  - IP pubblico: `[INSERIRE IP]`
  - Home directory sul VPS: `[INSERIRE PATH REMOTO]`
- **Tunnel SSH locali**:
  - n8n: `127.0.0.1:[PORTA_N8N]` → remoto `127.0.0.1:5678`
  - Firecrawl: `127.0.0.1:[PORTA_FIRECRAWL]` → remoto `127.0.0.1:3002`
  - OCR: `127.0.0.1:[PORTA_OCR]` → remoto `127.0.0.1:3033`

## Model team (configurato dall'utente)

- **Frontier (reasoning/architecture)**: `[INSERIRE MODELLO/CLI — es. Claude Opus, GPT-5, Gemini Pro]`
- **Frontier (orchestration/build)**: `[INSERIRE MODELLO/CLI]`
- **Mid-tier (component execution)**: `[INSERIRE MODELLO/CLI — es. DeepSeek V4-Pro]`
- **Frontier (terminal/sysadmin)**: `[INSERIRE MODELLO/CLI]`
- **Bulk (mechanical data)**: `[INSERIRE MODELLO/CLI — es. Gemini Flash, DeepSeek Flash]`
- **Local worker (fallback)**: `[INSERIRE MODELLO LOCALE — es. Ollama/Gemma, o "nessuno"]`

In MINIMAL con una sola CLI, puoi mappare più lane sullo stesso modello/CLI. In MULTI, di solito ogni lane corrisponde a una CLI diversa.

## Identity & Tone

- You operate inside the user's KnowledgeVault as a disciplined member of their agent team.
- Keep the user visible: close substantial work with a short summary.
- The default browser profile is the user's single working profile. Never drive it headless.
- `[AGGIUNGERE QUI LE PREFERENZE DI COMUNICAZIONE DELL'UTENTE (es. lingua, stile, formalità)]`
# NeXgen Engine — versione italiana

<p align="center">
  <picture>
    <source srcset="assets/nexgen-architecture-banner.webp" type="image/webp">
    <img src="assets/nexgen-architecture-banner.png" alt="NeXgen Engine — AI Operating Layer" width="100%" loading="eager">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/matteopasseri407/NeXgen-Engine/actions/workflows/ci.yml"><img src="https://github.com/matteopasseri407/NeXgen-Engine/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/matteopasseri407/NeXgen-Engine/releases/latest"><img src="https://img.shields.io/github/v/release/matteopasseri407/NeXgen-Engine?display_name=tag&label=latest%20version" alt="Ultima versione"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue" alt="Licenza"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-00E5B8?logo=python&logoColor=white" alt="Python 3.11+">
</p>

<p align="center">
  <a href="README.md">🇬🇧 Read in English</a> · <a href="#guida-rapida">Guida rapida</a> · <a href="docs/architecture-contract.md">Architettura</a>
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

## Principali Novità della Versione 2.0.4

* **Motore Unificato in Python (`nexgen_core`):** Esecuzione nativa multipiattaforma su Linux e Windows con oltre 400 test automatici, eliminando la duplicazione degli script di shell.
* **Layer Modulare Deterministico:** Catalogo di 8 moduli (`memory`, `semantic-rag`, `firecrawl`, `ocr`, `n8n`, `browser`, `council`, `sync`) gestito con i comandi `nexgen modules list` e `nexgen modules set`.
* **Deposito Segreti Age a Zero Passphrase:** Crittografia asimmetrica moderna (`99-SECRETS/secrets.yaml.age`) con chiavi hardware locali a permessi `0600`, slot OAuth per-host isolati contro i conflitti di rotazione dei token e file `secrets.env` per shell e servizi systemd.
* **Dashboard Grafica e Shell Operatore:** Comandi `nexgen info` per il riepilogo visivo dello stato e `nexgen shell` per una REPL interattiva a menu numerato (`[1-7]`), utilizzabile da un operatore umano senza aprire alcuna CLI di AI.
* **Supporto Completo per 4 Runtime:** Allineamento nativo per Claude Code, Codex, OpenCode e Antigravity (incluso il ruolo di seggio nel Consiglio AI).
* **Diagnostica Continua (`nexgen doctor`):** Batteria di oltre 33 controlli automatici su integrità Git, raggiungibilità dei server MCP, igiene dei collegamenti e permessi di sicurezza.

---

## Guida Rapida

### Opzione A — Installato (consigliata)

```bash
uv tool install nexgen-engine   # oppure: pipx install nexgen-engine
nexgen info
nexgen doctor
```

### Opzione B — Clonato

```bash
git clone https://github.com/matteopasseri407/NeXgen-Engine.git ~/KnowledgeVault
cd ~/KnowledgeVault
bash install.sh --check          # Windows PowerShell: .\install.ps1 -Check
```

### 1. Inizializzazione

Già eseguita dall'installer. Verifica:

```bash
nexgen sync
nexgen doctor --verbose
```

### 2. Configurazione Guidata

Apri `INIT.md` e incolla il testo nella tua CLI preferita (Claude Code, Codex, OpenCode o Antigravity). L'agente configurerà il profilo e i moduli desiderati.

### 3. Allineamento e Verifica

```bash
nexgen sync
nexgen doctor
```

### 4. Gestione da Terminale

```bash
nexgen info
nexgen shell
```

---

## Licenza

PolyForm Noncommercial License 1.0.0. Gratuito per qualsiasi uso non commerciale, studio e deployment self-hosted. Consulta il file `LICENSE` per il testo completo. Per uso commerciale vedi `COMMERCIAL.md`.

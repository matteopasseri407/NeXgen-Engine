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

**Un solo repo Git che configura ogni CLI di coding AI su ogni macchina — e ne verifica il risultato.**

NeXgen Engine è un control layer deterministico che mantiene identici istruzioni, configurazione degli strumenti, segreti e memoria versionata tra Claude Code, Codex, OpenCode e Antigravity.

Le configurazioni delle CLI divergono da macchina a macchina. NeXgen tiene un'unica sorgente di verità in Git, la compila nei formati nativi di ogni assistente e ne verifica il risultato con controlli automatici che bocciano invece di far finta che vada tutto bene.

---

## I Tre Piani Architetturali

NeXgen organizza il lavoro degli agenti in tre piani separati:

1. **Comportamento (Behavior):** Regole operative universali, prompt e guardrail immutabili definiti in `AGENTS.md` e collegati a ogni runtime.
2. **Configurazione (Configuration):** Manifest astratti di connettori MCP e skill, compilati in modo deterministico nei formati delle varie CLI tramite `nexgen sync`.
3. **Memoria (Memory):** KnowledgeVault in puro Markdown con blocco atomico compare-and-swap (CAS), aggiornamenti per singola sezione (`update_section`) e cronologia Git completa.

---

## Cosa fa

* **Core unico in Python (`nexgen_core`):** gira nativo su Linux e Windows, senza gemelli shell. Suite automatizzata in CI.
* **Moduli deterministici:** catalogo di 9 moduli (`memory`, `semantic-rag`, `firecrawl`, `ocr`, `n8n`, `browser`, `council`, `local-lane`, `sync`) gestito con `nexgen modules list` e `nexgen modules set`.
* **Lane locale governata (opzionale):** i modelli locali piccoli lavorano in sola lettura tramite `nexgen-local` — query costruite dal motore, ricevute su audit fail-closed e suite trappole bloccante: una sola injection o confabulazione boccia. Le proposte di patch passano da un cancello a fatti macchina (`nexgen-local propose` / `apply`). Vedi `docs/local-lane.md`.
* **Segreti:** cifratura asimmetrica `age` (`99-SECRETS/secrets.yaml.age`) su chiavi locali (`0600`), slot OAuth isolati per host, `secrets.env` materializzato per shell e servizi. Niente passphrase da ricordare o digitare.
* **Shell operatore:** dashboard `nexgen info` e REPL interattiva `nexgen shell`, così la gestione ordinaria non richiede mai una CLI AI aperta.
* **Quattro runtime:** Claude Code, Codex, OpenCode (nativo V2: istruzioni, `plugins`/`permissions`, viste skill) e Antigravity, ognuno nel suo dialetto, seggi Council inclusi.
* **Diagnostica che boccia (`nexgen doctor`):** controlli automatici su allineamento Git, manifest, igiene dei link, token e permessi. Un controllo che non può verificare dichiara esito indeterminato invece di passare.

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

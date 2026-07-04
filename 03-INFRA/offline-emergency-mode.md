---
tags:
  - infra
  - agents
  - runbook
status: active
type: reference
last_updated: <past-date>
---

# Modalità emergenza — Remote backend down o macchina offline

When the remote backend (<remote-host>) è irraggiungibile cadono INSIEME: scritture note (MCP vault-library), RAG semantico, firecrawl (lane web di default) e n8n. Il layer locale continua a funzionare in lettura. Questo runbook dice cosa fare e cosa non fare, senza improvvisare.

## Prima cosa: capire cosa è giù

- Internet c'è ma `ssh <remote-alias>` fallisce: the VPS is down, vedi «Remote recovery» in fondo.
- Niente rete del tutto: macchina offline, modalità qui sotto.

## Cosa funziona ancora

- Il MIRROR locale del vault, in LETTURA completa: note, skill, INDEX, manifest.
- Gli agenti CLI coi modelli cloud, se internet c'è but the remote backend is not.
- Sul fisso Windows, se anche internet è giù: Gemma 4 12B via Ollama è l'unico agente operativo (istruzioni in `agent-universal-layer/instructions/LOCAL-WORKER.md`).

## Regole in emergenza

1. NIENTE scritture nel mirror locale del vault: resta read-only anche in emergenza.
2. Il lavoro prodotto si parcheggia APPEND-ONLY fuori dal vault, in `~/vault-outbox/` (Windows: `%USERPROFILE%\vault-outbox\`), un file per argomento, `AAAA-MM-GG-argomento.md`, contenuto già compresso e pronto da riversare. Creare la cartella se manca.
3. Web: firecrawl giù, quindi è consentito l'headless locale read-only e anonimo (carve-out già previsto dal bootstrap). Tutto ciò che è interattivo o autenticato resta nel Chrome visibile condiviso.
4. n8n giù: non replicare workflow in locale, annotare nella outbox e aspettare.
5. Niente rotazioni di segreti o modifiche di config in emergenza.

## Re-entry, when the remote backend returns

1. `agent-sync` (pull del mirror aggiornato).
2. Riversare i file di `~/vault-outbox/` nel vault VIA MCP (`append_note`, o `update_note` con `expected_hash`), poi svuotare la outbox.
3. `agent-doctor` per confermare l'allineamento.

## Remote recovery (se è la VPS a essere giù)

- Precedente noto: OOM da container senza cap di memoria, a past incident (vedi [[<remote-alias>]] e la nota outage). Never launch containers without `--memory`.
- Riavvio dalla console web the cloud provider console, poi `ssh <remote-alias> "sudo docker ps"` e healthcheck dei container (`vault-mcp`, `vault-semantic`, `n8n`, stack firecrawl).

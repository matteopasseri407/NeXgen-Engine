# Architettura di scrittura del KnowledgeVault (Vault 2.0 — Fase 3)

Principio: **una porta per tipo di cosa.** Cloud-first: the remote backend is the source of truth, il filesystem locale è mirror in sola lettura + paracadute offline.

## Le due porte

- **Note / conoscenza (markdown)** → SOLO via MCP `vault-library` (`create_note`, `append_note`, `update_note`). L'MCP serializza con lock (`flock`) e `expected_hash`, e committa direttamente to the remote bare repo come autore "Vault MCP". Gli agenti **non committano note a mano con git**.
- **File infra (script, manifest, hook, config)** → `vault-push -m "messaggio" <file...>`: commit git + push to the remotes con rebase pulito, STOP sicuro sui conflitti veri (non forza mai).

## Componenti vivi
- **MCP `vault-library`** (remote backend, container `vault-mcp` :rw): scritture note serializzate, commit al bare.
- **`cloud-pull.service`** (enabled): aggiorna il mirror locale via pull from the remote backend.
- **`agent-sync.timer` / task Windows `KnowledgeVault Agent Sync`**: modalità `guard`, quindi pull cloud + propagazione automatica dei derivati runtime + healthcheck, senza push automatico. `apply` è l'alias manuale di guard. Pubblicazione commit locali già fatti: `publish` o `vault-push`. Vecchio giro completo: `full`, manuale.
- **`vault-push`** (`03-INFRA/scripts/vault-push.sh`, symlink in `~/.local/bin`): pubblicazione dei file infra.

## Dismesso
- **`autosync.service`** (watchdog filesystem che auto-committava ogni 60 s): RIMOSSO il <past-date>. Era la "seconda porta" che generava commit ciecamente. Codice inerte lasciato in `~/.local/share/knowledge-vault-autosync`, non eseguito.

## Regole d'oro
1. Una fonte di verità per ogni cosa; il resto è generato o mirror, read-only.
2. Note → MCP; infra → `vault-push`. Mai due porte sulla stessa cosa.
3. Dati volatili (es. agenda calendario) NON si versionano: si leggono al volo dai connettori MCP. Il workflow n8n "Calendario -> Vault Sync" è stato archiviato per questo.

## Follow-up noti
- Move any plaintext tokens from CLI settings into env vars, so no config file holds a secret literally.
- Windows: split `guard/publish/full` portato in `agent-sync.ps1`, ma l'aggancio completo di `render.py` al posto della sezione MCP hardcoded resta follow-up del progetto Vault 2.0.

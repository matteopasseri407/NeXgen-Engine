# Mappa dei guardiani del layer agenti

Complemento operativo di [[agentic-layer-concept-map]]: la vista "osservabilità" del layer, cioè chi esegue, chi diagnostica e chi avvisa. Nato dalla domanda "quante sentinelle ci sono e come si allineano?" (2026-07-04). Versione visuale (artifact): https://

## Regola d'oro

Un posto solo decide se qualcosa è rotto, un posto solo te lo dice, tutti gli altri eseguono in silenzio.

## I tre ruoli

- **Un cervello, `agent-doctor`.** La diagnosi completa: git vs remote, MCP raggiungibili, drift MCP via `render.py`, istruzioni canoniche, token in env, skill, worker locale risolto, hook Claude, strict-check dei consumatori reali. È l'unico giudice e l'unico comando da lanciare a mano: `agent-doctor` (o `--summary` per la riga `PASS/WARN/FAIL`).
- **Un megafono, `agent-healthcheck`.** L'UNICO autorizzato a notificare. Interroga il dottore, avvisa solo su FAIL, con debounce (subito se il problema è nuovo, 1 volta al giorno se persiste), messaggio in italiano semplice con `[tecnico: ...]` in coda. Trasporto in ordine: Telegram, webhook, `notify-send` desktop, log.
- **Un orologio, `agent-sync.timer`** (Linux ogni 30 min, Windows task schedulato). L'unico scheduler ricorrente. Lancia `agent-sync guard` = pull + apply dei derivati + healthcheck. `agent-sync` esegue e logga soltanto, NON notifica.

## Consolidamento 2026-07-04 (un solo megafono)

Fino al 3/7 `agent-sync` notificava il drift MCP per conto suo (sentinella inline, senza debounce, mittente separato), duplicando ciò che `agent-doctor` già calcola e `agent-healthcheck` già annuncia a fine giro. Rimossa: ora esiste UNA sola superficie di alert. `agent-sync` è muto. Verificato: l'unico `notify-send` del layer è in `agent-healthcheck.sh`.

## Flusso del giro automatico

`agent-sync.timer` fa partire `agent-sync guard`:

1. pull the vault from the remote
2. apply: config MCP (`render.py`, additivo) + skill (`skills-sync.py`)
3. `agent-healthcheck` interroga `agent-doctor` e notifica SOLO se FAIL

## Inventario

| Guardiano | Ruolo | Cosa fa |
|---|---|---|
| `agent-sync` (+ `.timer`) | orologio | giro ricorrente: pull, apply config e skill, chiama l'healthcheck. Muto. |
| `agent-doctor` | cervello | diagnosi completa dell'allineamento; unico giudice |
| `agent-healthcheck` | megafono | notifica solo su FAIL, con debounce e formato umano; unico alert |
| `render.py` | esecutore | genera le config MCP dal manifest (additivo); calcola il drift |
| `skills-sync.py` | esecutore | propaga le skill dal manifest a tutte le macchine |
| `skill-check` | esecutore | controllo advisory di sicurezza di una skill (SkillSpector) |
| `sync-vault-from-remote` | esecutore | pull the vault from the remote before apply |
| `n8n-vault-backup` | esecutore | backup notturno dei workflow n8n (cron on the remote backend) |
| `sync-job-pipeline` | esecutore | aggiorna la dashboard della ricerca lavoro |
| tunnels to the remote | esecutore | tunnel SSH persistenti (OCR, n8n, firecrawl) |

## Principio di estensione

Un controllo nuovo va DENTRO `agent-doctor`, non in un nuovo script che notifica. Una cosa nuova da dire a the user passa da `agent-healthcheck`. Mai aggiungere `notify-send` altrove: spezzerebbe il megafono unico e riporta il rumore sparso che questo consolidamento ha eliminato.

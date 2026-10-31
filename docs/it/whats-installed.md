# Cosa è installato, e come tenerlo aggiornato

🇬🇧 [English](../whats-installed.md)

`nexgen info` elenca i server MCP e le skill presenti su questa macchina: di chi è ciascuno, a cosa è pinnato e se
a monte qualcosa si è mosso. Legge solo file locali. La risposta su cosa si è mosso a monte è quella che l'ultimo
controllo orario delle dipendenze ha lasciato, quindi `info` risponde subito e non tocca mai la rete.

## Di chi è un elemento

Una parola per server e per skill.

| Parola | Significato | Cosa lo aggiorna |
| --- | --- | --- |
| `core` | Arriva con il motore. | `nexgen update`: lo porta il motore. |
| `yours` | Vive nel tuo Vault o sul tuo disco. | Tu. Nessun altro lo tocca. |
| `third-party` | Pacchetto, repository o servizio di qualcun altro. | `nexgen mcp bump` / `nexgen skills bump`, dopo che il guardiano l'ha esaminato. |

Un server si giudica da come parte. Un launcher di pacchetti (`npx`, `uvx`, `docker`, ...) è di terze parti qualunque
cosa gli venga passata come argomento; un percorso sotto `${AGENT_ENGINE_ROOT}` o un comando `nexgen-*` è core; un
percorso nel tuo Vault o un percorso assoluto sul tuo disco è tuo. Una skill si giudica dal suo `origin`: `engine` è
core, `vault` è tua, `github`, `installer` e `upstream` sono di terze parti. C'è un caso che prevale: una skill che il
motore distribuisce, tenuta nel Vault con `origin: vault`, conta come core, e `info` dice se la copia nel Vault
corrisponde ancora a quella del motore.

## Cosa dice ogni riga

Per un server: dove è montato (`direct`, `lazy` per dietro il gateway, `mixed`, `off`, `in prova`/`trial`, con tra
parentesi le CLI a cui è limitato), la versione a cui è pinnato e, sotto, le note:

- `upstream X available (pinned Y)`, più se il guardiano ha autorizzato il cambio o lo trattiene, con il motivo
  detto in parole semplici;
- `not pinned`: un server `npx` senza `package@version` esegue quello che il registro serve oggi, e il controllo
  delle dipendenze non lo vede. Pinalo nel manifest.
- `N tools hidden`: il `tools_deny` della voce (vedi [lazy-mcp.md](lazy-mcp.md)).
- perché un server non è montato da nessuna parte.

Per una skill: origine e pin, se a monte si è mosso qualcosa, se è dichiarata ma non installata qui e, per una skill
core con una copia nel Vault, se la copia differisce da quella del motore. Quella che gira è la copia, quindi non segue
gli aggiornamenti del motore: identica oggi, vecchia dopo la release successiva. `nexgen skills adopt` te la
restituisce (sotto).

Le skill che sono semplicemente tue compaiono come numero; `nexgen info --all` le elenca tutte. `nexgen info --json`
porta gli stessi dati sotto `extensions`.

## Aggiornare

`nexgen mcp bump` e `nexgen skills bump` sono lo stesso comando. Mostra ogni aggiornamento che il guardiano ha
autorizzato, in parole semplici, e chiede una volta sola. Gli aggiornamenti che richiedono un occhio umano restano
trattenuti e non vengono toccati.

Per i server MCP fa una cosa in più prima di tenere il cambio: avvia ogni server il cui pin si è mosso, esattamente
come farebbero le CLI, e controlla che elenchi i suoi tool. Se uno non funziona, ogni pin torna dov'era e le
configurazioni delle CLI vengono rigenerate da quelle vecchie; non si committa niente. Dietro il gateway, un server a
cui in questa sessione manca solo una credenziale non conta contro l'aggiornamento.

## Le skill che distribuisce il motore

Le installazioni più vecchie copiavano le skill iniziali del motore nel Vault e dichiaravano le copie `origin: vault`.
Una copia si congela il giorno in cui nasce: la release successiva corregge la skill, la copia tiene il testo vecchio,
e ogni CLI esegue la copia. `origin: engine` collega invece la cartella del motore, quindi `nexgen update` è l'unico
passo.

`nexgen skills adopt` elenca le copie e dice quali sono identiche a quelle del motore e quali no.
`nexgen skills adopt --all` converte quelle identiche: cambia la riga `origin` della voce (nient'altro nel manifest,
commenti compresi), sposta la copia del Vault in una cartella di backup nella directory di stato della macchina e
ricollega. Una copia che differisce resta com'è, perché può contenere qualcosa che hai scritto tu; guarda cosa
differisce, poi nominala con `--force` se il testo del motore è quello che vuoi. `nexgen doctor` avvisa finché una
copia del genere esiste, e `doctor --fix` converte quelle identiche. Pubblica il risultato con `nexgen vault push`
così le altre macchine seguono.

Una skill che vuoi tenere a modo tuo resta `origin: vault` ed è semplicemente tua: nessuno la tocca e smette di essere
segnalata quando non condivide più il nome con una skill del motore. Rinominala per dirlo.

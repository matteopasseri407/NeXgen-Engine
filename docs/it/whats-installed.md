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

## Cosa si aggiorna da solo e cosa aspetta te

Ogni voce di terzi segue una di cinque strade, e `nexgen thirdparty status` stampa, una riga per voce, quale vale per
quella voce, a che versione è ferma, quale è la più recente a monte, quando è stata controllata l'ultima volta e il
comando esatto che la muove. Chiedi prima quel comando.

| Politica stampata | Cosa significa | Come si muove |
| --- | --- | --- |
| `si alza da sola` | I byte sono identici fra la versione vecchia e la nuova, quindi niente può cambiare. | La alza il controllo orario e committa. Non devi fare niente. |
| `aspetta il tuo sì` | Solo prosa, un commit discendente, o una patch npm: forma sicura e piccola. | `nexgen skills bump`, un sì per tutte. |
| `da guardare prima` | Uno script nuovo, un hook toccato, una minor o una major, una storia riscritta, o una versione ritirata da chi pubblica. | **Nessun comando la alza.** Leggi cosa è cambiato, poi modifica il pin nel file che la riga nomina. |
| `a mano, dove gira` | Un componente dentro un modulo: l'immagine n8n, il lanciatore Playwright, OpenClaw stesso. | Si sostituisce dove gira, dopo averlo provato. Il motore non li riscrive mai. |
| `fluttuante, senza pin` | Un server `npx` senza `package@versione`, o un endpoint servito da altri (`mcp.vercel.com`). | Già adesso cambia senza che qui nessuno decida. Lo aggiorna chi lo serve, oppure fissi una versione per riprenderti la scelta. |

Tre cose che sorprendono:

- **Con `nexgen sync` non si aggiorna niente.** Il sync materializza il pin scritto nel manifest. Una skill o un
  server bloccato a una versione sono identici prima e dopo il sync, qualunque cosa abbia pubblicato chi sta a monte.
- **Una versione più nuova non è un aggiornamento approvato.** Il controllo delle dipendenze dice solo che qualcosa si
  è mosso; il guardiano decide se il movimento è banale. `nexgen info` conta quello che si è mosso, quindi il suo
  numero può essere più grande di quante cose aspettano te.
- **`nexgen skills bump` che non fa niente è una risposta, non un guasto.** Stampa quante voci restano ferme, e
  `nexgen thirdparty status` dice cosa serve a ciascuna.

`nexgen doctor` porta una riga per tutto questo: quante cose sono già approvate e aspettano un sì (con il comando che
le applica), oppure quante vanno guardate prima. Non diventa rosso per un aggiornamento in attesa, e `doctor -v` la
stampa.

### Da dove vengono queste meccaniche

Il motore decide cosa governa una voce e tiene il manifest; la meccanica di una certa sorgente è sempre più spesso di
qualcun altro, ed è voluto: un progetto mantenuto che si occupa di installare una cosa sola se ne accorge prima di
questo motore.

- **Una skill da github** la scarica la [CLI `skills`](https://github.com/vercel-labs/skills) (MIT, fissata a
  npm `skills@1.8.1`) quando Node è installato: risolve il commit scritto nel manifest e scarica quella sola cartella
  invece di tutto il repository, e la cartella che lascia è quella che il motore copia in libreria. Il controllo di
  integrità è il suo `skills-lock.json`, che deve nominare il commit fissato e un hash del contenuto, altrimenti i byte
  vengono rifiutati. Una macchina senza Node clona il repository al pin, esattamente come prima; una CLI assente, che
  fallisce o che risponde con un altro commit non costa niente, perché il clone segue. Il suo `update` non viene usato:
  muove le proprie installazioni e le proprie cartelle delle CLI, quindi sarebbe un secondo scrittore degli stessi byte.
  A muovere resta `nexgen skills bump`.
- **Un plugin** appartiene alla CLI che lo ha installato, e il motore lo riporta invece di copiarne l'aggiornamento:
  `nexgen thirdparty status` finisce con i plugin che Claude Code, Codex e Antigravity elencano con il proprio
  `plugin list --json`, ognuno con il comando che lo muove (`claude plugin update <plugin>`,
  `codex plugin marketplace upgrade`, `agy plugin install <plugin>`). Niente di tutto questo viene eseguito dal motore.
- **La versione di un server MCP** si legge da npm e da nessun'altra parte. Il registry ufficiale MCP è stato misurato
  contro npm ed è stato rifiutato: è indietro (il server di un publisher era quattordici release dietro), e due dei
  server fissati su questa macchina non sono elencati sotto il nome del loro pacchetto, quindi leggerlo avrebbe nascosto
  proprio le release che il controllo esiste per riportare.

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

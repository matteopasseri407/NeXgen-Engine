# Aggiornare il motore

🇬🇧 [English](../upgrade.md)

Questa guida copre le installazioni da Git. Se hai installato il pacchetto con `uv` o `pipx`, aggiornalo con quel
gestore di pacchetti: `nexgen update` richiede un checkout Git del motore.

Un solo clone, come `~/KnowledgeVault`, può contenere sia il codice del motore sia i tuoi dati privati. Un'installazione
separata li tiene in clone distinti, scelti da `AGENT_ENGINE_ROOT` e `AGENT_VAULT_DATA`. L'updater gestisce entrambe le
disposizioni, e un'installazione separata registra la versione del motore in `99-INDEX/ENGINE-PIN.txt`.

Se stai aggiungendo un PC esistente a un gateway centrale, vedi
[Ruoli della macchina](roles.md#hai-già-nexgen-su-questo-pc).

## Perché tenere traccia della versione

I tuoi dati (manifest, istruzioni, skill) sono scritti contro un comportamento noto del motore. Prendere ogni volta
l'ultimo `main` cambierebbe la tua configurazione in esecuzione senza che tu l'abbia deciso. Il file `VERSION` nel tuo
clone, che `cat VERSION` stampa, è la versione che stai davvero eseguendo; spostala solo quando lo scegli tu.

## Capire se c'è un aggiornamento

`nexgen upgrades`, equivalente a `nexgen update --check`, funziona su entrambe le topologie: scarica i tag di `origin`,
in sola lettura, e riporta se esiste un tag rilasciato più nuovo del tuo `VERSION`. La modalità di controllo lascia
intatta la versione installata. Il battito pianificato tenta separatamente aggiornamenti automatici non presidiati, entro il
tetto delle release patch. `nexgen doctor` controlla l'allineamento; è il comando di aggiornamento a dirti di una
release più nuova.

Per controllare a mano:

```bash
cd ~/KnowledgeVault   # o dove l'hai clonato
git fetch --tags origin
git tag --merged origin/main --sort=-v:refname | head -1   # ultimo tag rilasciato
cat VERSION                                                  # la versione che stai davvero eseguendo
```

Se l'ultimo tag è più nuovo di `VERSION`, c'è un aggiornamento.

## Canali

Le build di sviluppo sono per chi lavora al motore. Il valore predefinito è `stable`, che segue i tag rilasciati di
`main`. Un'installazione apre `nightly` o `beta` nella propria configurazione privata
(`update.allow_channels: [nightly, beta]` in `sync/remotes.yaml`) e ricorda la scelta con
`nexgen update --channel nightly`. `nightly` segue la punta di `developer` solo quando è firmata e i suoi cancelli CI
(leak scan, suite Linux, gate Windows veloce) sono verdi su quel commit. `beta` segue solo l'ultimo commit firmato che
la suite nightly completa ha dimostrato su Windows e Linux.

## Avviso all'avvio (un invito al giorno, lancia o salta)

Ogni shell interattiva può dirti che c'è un aggiornamento in attesa e proporti di lanciarlo. La guardia installa
entrambe le corsie a ogni apply, su Linux e Windows: hook di shell più controllo all'avvio, cioè autostart XDG e un
timer di boot su Linux e un'attività all'accesso su Windows. Per installarli o rimuoverli a mano:

```bash
nexgen tool update-notifier --install-shell-hook   # bash + PowerShell
nexgen tool update-notifier --install-autostart    # corsia con finestra grafica (Linux)
```

Le richieste grafiche portano il logo del motore (`assets/nexgen-logo.jpg`, risolto dal checkout quando la finestra si
apre, con l'icona di sistema come ripiego se il checkout non ha gli asset): finestre zenity su Linux tramite
`--window-icon` e `notify-send` tramite `--icon`, toast passivi su Windows tramite BurntToast `-AppLogo`. Il logo viaggia
su notifiche che vengono già mostrate, e nessuna icona fissa viene installata da nessuna parte. Le finestre bloccanti
`MessageBox` restano solo testo, perché quell'API non può portare un'immagine propria.

Da lì ogni nuova shell esegue `--shell-check`: una lettura di cache, mai la rete, e una cache vecchia si aggiorna in
sottofondo staccata. Quando c'è un aggiornamento in attesa ricevi una riga più `Aggiorna ora? [s/N]`, al massimo una
volta al giorno per versione. "Sì" passa il terminale al `nexgen update` interattivo, prima le note di rilascio e poi
la sua conferma. "No" resta zitto fino a domani. La cache viene aggiornata dal ciclo di guardia e dal battito orario,
con una lettura dei tag in sola lettura, silenziosa quando sei offline, così l'invito non tiene mai in ostaggio l'avvio
di una shell. Per toglierlo:
`nexgen tool update-notifier --install-shell-hook --remove`.

## Aggiornare

La strada normale è il comando multipiattaforma che installa il provisioner MULTI:

```bash
nexgen update --check
nexgen update
```

(Nome storico, ancora installato e funzionante: `nexgen-update`.)

Il primo comando scarica i tag rilasciati e stampa il changelog senza spostare il ramo né cambiare file installati. Il
secondo ripete i controlli, verifica che sia il clone del motore sia il clone separato dei dati siano puliti, mostra il
piano di merge, provisioning e doctor, e chiede conferma. In automazione, `nexgen update --yes` è la conferma esplicita,
e `nexgen update --unattended` in più rifiuta ogni salto più grande di una release patch. Una macchina da maintainer può
alzare il tetto alle release minor con `nexgen update --set-unattended-ceiling minor`, salvato nella directory di stato
di questa macchina; `patch` lo riporta indietro. Una release major non viene mai presa in automatico.

Scopre la radice dei dati in questo ordine: `AGENT_VAULT_DATA`, `KNOWLEDGE_VAULT_PATH`, poi `~/KnowledgeVault` quando
quel valore predefinito è un checkout Git. Se non ne esiste nessuna, tiene il checkout del motore come radice dei dati.
Usa un fast-forward per un motore consumer separato. Nel clone unico predefinito permette un normale commit di merge,
così i commit dei dati privati restano sul ramo agganciato. Quando un clone dati separato ha già
`99-INDEX/ENGINE-PIN.txt`, il comando committa solo quel pin meccanico tramite `nexgen vault push` (nome storico
`vault-push`) prima del provisioning. Poi cerca ed esegue il launcher `agent-sync` (`agent-sync apply`, a cui risponde
anche il nome storico `nexgen sync`), confronta il doctor prima e dopo, e non fa mai stash, reset o rollback del lavoro
tuo da solo.

Prima di fare il merge verifica la firma del tag di release contro i firmatari bloccati nella copia che hai già
installata (vedi [SECURITY.it.md](../../SECURITY.it.md)). Una firma sbagliata, o di una chiave che quella copia non blocca,
ferma l'aggiornamento in ogni modalità con uscita `70`. Una release che non riesce a verificare affatto, perché non c'è
firma o non c'è lo strumento di verifica sulla macchina, si installa comunque con una riga semplice che lo dice, in ogni
modalità compresa `--unattended`: una macchina che non può controllare ha comunque bisogno dei suoi aggiornamenti, e una
domanda che l'installer non sa valutare insegnerebbe solo a confermare alla cieca. Il merge aspetta che un sync in corso
finisca, invece di fondere sotto di esso.

`--unattended` è l'unica modalità che disfa il proprio lavoro. Se l'aggiornamento fallisce dopo che il motore si è
mosso, torna alla versione che funzionava, ripristina il pin del motore, ricorda la release come rifiutata così il
battito successivo non la ritenta ogni ora, e lancia un allarme. Esegui `nexgen update --target <version>` tu stesso per
riprovare quella release. Codici di uscita: `70` rifiutata per la firma, `71` fallita e disfatta, `72` fallita e non
disfacibile, con il recupero manuale stampato.

### L'ambiente Python del motore

LangGraph, LangChain e `mcp` sono dipendenze fondamentali: il relay riprendibile del Council, la corsia locale e i
server MCP girano su di esse, e il `python3` di sistema di solito non ne ha nessuna. Per un checkout la guardia
prepara quindi un ambiente proprio, `~/.local/share/nexgen-engine/runtime`, fuori da ogni albero git così non rende mai
sporco il checkout, dall'elenco delle dipendenze in `pyproject.toml`. I launcher lo usano appena è stato preparato e
verificato, e fino ad allora ripiegano su un Python adatto qualsiasi, così la guardia e il doctor continuano a non aver
bisogno di altro che PyYAML. `nexgen runtime check` dice se l'interprete che stai eseguendo riesce a importare tutto,
`nexgen runtime ensure` crea o ripara l'ambiente a mano, e `nexgen doctor` segnala una libreria mancante come guasto,
con quel comando. Un'installazione da pacchetto è già un ambiente proprio e viene solo verificata. Quando `uv` è nel
`PATH` quell'ambiente lo costruisce lui, con `uv venv` e `uv pip install`; altrimenti il motore lo crea con
`python -m venv` e lo riempie con pip, e le due strade finiscono sullo stesso timbro e sullo stesso controllo degli
import.

Un aggiornamento che aggiunge una dipendenza non ti chiede niente. Il primo comando del nuovo albero che gira in
quell'ambiente e trova mancante una libreria dichiarata installa l'elenco delle dipendenze e si rilancia, così
l'aggiornamento che porta la 3.0 (filelock, psutil e tomlkit sono nuove) le installa strada facendo, e un'installazione
da zero crea l'ambiente quando manca una qualsiasi libreria dichiarata, non solo PyYAML. Il salto a una nuova versione
maggiore non avviene mai da solo: lancia `nexgen update` una volta da un terminale, dove vedi il piano e rispondi alla
conferma.

La sequenza manuale qui sotto resta la strada di recupero per le installazioni MINIMAL e per l'unico aggiornamento di
bootstrap da una versione precedente al comando.

1. Leggi `CHANGELOG.md` per le versioni tra il tuo `VERSION` attuale e quella verso cui ti muovi. Guarda le sezioni
   `### Changed` e `### Removed`; `### Added` è sempre sicura.
2. Assicurati che l'albero di lavoro sia pulito (`git status`). Il tuo contenuto del Vault, come `01-NOTES/`,
   `02-PROJECTS/`, `99-INDEX/USER-PROFILE.md` e `03-INFRA/agent-universal-layer/skills/skills.manifest.yaml`, vive come
   normali commit in questo stesso clone, quindi committa o metti da parte ciò che è in corso prima di spostare il ref.
3. Scarica e porta dentro il tag di destinazione:
   ```bash
   git fetch --tags origin
   git merge vX.Y.Z
   ```
   Preferisci `merge` a `git checkout vX.Y.Z`: un checkout nudo stacca `HEAD` e lascia i tuoi commit locali su `main`,
   le tue note e il tuo profilo, fuori dal ramo. Un merge li tiene su `main` e ti chiede di risolvere un conflitto solo
   se la nuova versione del motore e una tua modifica hanno toccato lo stesso file, cosa che non dovrebbe accadere se
   tieni le personalizzazioni nei tuoi dati (note, manifest, `USER-PROFILE.md`) invece di modificare a mano gli script
   del motore.
4. Esegui un passaggio di provisioning e controlla il risultato.
   - Profilo MULTI:
     ```bash
     nexgen sync
     nexgen doctor --strict --summary
     ```
     (Nomi storici, ancora funzionanti: `agent-sync apply`, `agent-doctor --strict --summary`.)
     `nexgen sync` dimostra prima che il ramo dei dati è fresco rispetto al remoto autorevole dichiarato in
     `03-INFRA/agent-universal-layer/sync/remotes.yaml`. Poi valida il contratto di configurazione prima di scrivere un
     file generato per le CLI. Stati Git non sicuri e configurazione non valida fermano il sync. Vedi
     [sync-contract.md](sync-contract.md).
   - Profilo MINIMAL: non c'è nessun `nexgen sync` o `nexgen doctor` da eseguire, perché MINIMAL non li installa mai.
     La diagnostica è visiva: apri la CLI che hai configurato e conferma che carichi ancora `AGENTS.md`, che monti
     ancora i server MCP che ti aspetti e che veda ancora le tue skill.
5. Se `nexgen doctor` riporta nuovi `FAIL` che prima dell'aggiornamento non c'erano (MULTI), o la tua CLI smette di
   comportarsi come prima (MINIMAL), qualcosa nella nuova versione non si adatta al tuo setup. Torna indietro con
   `git reset --hard <commit-o-tag-precedente>` e segnala cosa si è rotto.

## Se usi un'installazione Cloud-Server, il server segue il motore da solo

Tutto quanto sopra aggiorna la macchina su cui l'hai digitato. I container sotto `03-INFRA/deploy/` (la porta di
scrittura `vault-mcp` con cui parla `vault-library`, l'API OCR, n8n) girano sul server, e continuavano a eseguire il
codice con cui erano stati costruiti mentre il motore andava avanti. Tre cose ora tengono insieme le due metà.

**L'immagine dice da cosa è stata costruita.** `nexgen deploy vault-mcp` costruisce il container con dentro il commit del
motore, e il servizio pubblica quel commit breve sulla sua rotta di salute non autenticata. Nient'altro viene pubblicato lì.

**`nexgen doctor` misura la distanza.** Su ogni macchina che dichiara il ruolo server o un'installazione Cloud-Server
stampa una riga `deploy.alignment` che confronta tre commit: quello di questo motore, quello registrato in
`99-INDEX/ENGINE-PIN.txt` per la flotta, e quello che il `vault-mcp` distribuito dichiara. Se coincidono la riga è verde.
Un server costruito da un altro commit è un guasto che nomina entrambi e l'unico comando che lo chiude:

```bash
# sul server
nexgen deploy vault-mcp
```

**Un aggiornamento sul server ridistribuisce lo stack che ha spostato.** Dopo un `nexgen update` riuscito su una macchina
con il ruolo server, se la release ha cambiato `03-INFRA/deploy/vault-mcp` il comando ricostruisce quel solo servizio e lo
sonda, anche negli aggiornamenti non presidiati, perché su una VPS senza sessione è il timer la via che aggiorna il
motore. Un aggiornamento che non ha toccato lo stack non riavvia niente. La ricostruzione avviene *prima* del controllo
finale del doctor, quindi un server non resta mai a metà: una ricostruzione che fallisce rimette il proprio container
sull'immagine che funzionava, e l'aggiornamento viene disfatto per intero.

Il deploy è lo stesso comando a mano, ed è scritto per non essere sorvegliato. Mette da parte il sorgente attuale, tiene
l'immagine in esecuzione con il tag `<immagine>-pre-deploy`, ricostruisce **solo** il servizio `vault-mcp`, e poi chiede
al server ricostruito le due chiamate MCP che fa un client (l'handshake e la lista dei tool). Se quella sonda fallisce
rimette l'immagine precedente, ripristina il sorgente salvato ed esce con un codice diverso da zero dicendolo. Una
ricostruzione è un riavvio della porta di scrittura: le note sono committate a ogni scrittura, quindi niente di ciò che è
in corso va perso, ma una chiamata a un tool in corso fallisce. Fai il deploy quando non sei a metà sessione.

Due cose restano manuali, e il doctor lo dice invece di far finta del contrario. L'API OCR e n8n non hanno una sonda che
dichiari il loro commit, quindi una release che li tocca è ancora segnalata da `CHANGELOG.md` e distribuita rieseguendo
`bootstrap-vps.sh`; e un server il cui container è stato costruito da una copia del sorgente fuori dal checkout nomina
quella cartella una volta con `NEXGEN_VAULT_MCP_SRC`, che il deploy riaggiorna dall'albero del motore a ogni esecuzione.

Un'installazione Local-Only non ha niente di tutto questo: nessuna VPS, nessun `deploy/`, niente da aggiornare due
volte.

## Se usi Local-Full, anche il tuo stack Docker è una seconda installazione

Lo stesso rischio di deriva vale una macchina più vicino. In modalità Local-Full gli stessi connettori sotto
`03-INFRA/deploy/` girano sul Docker di questa macchina invece che su una VPS, avviati con `nexgen stack up`. Un
`nexgen sync` o `nexgen update` qui aggiorna il codice del motore e la configurazione delle CLI, ma non riavvia né
ridistribuisce i container in esecuzione, e `nexgen doctor` non ne parla: `nexgen deploy` è il comando del server, e una
postazione di lavoro riavvia il proprio stack con `nexgen stack up`, che riapplica le definizioni compose attuali. Tieni
d'occhio `CHANGELOG.md` per qualsiasi cosa nomini `deploy/`, poi rieseguilo. `nexgen stack status` mostra cosa sta davvero
girando.

## Campi del manifest e del manifest delle skill

Il manifest MCP e quello delle skill vengono letti con tolleranza: un aggiornamento del motore che aggiunge un campo
facoltativo a uno dei due schemi non richiede niente da parte tua, perché un lettore più vecchio, da entrambe le parti,
ignora una chiave che non riconosce invece di rifiutare il documento (vedi [sync-contract.md](sync-contract.md),
"Configuration gate"). Oggi non esiste altro meccanismo automatico di migrazione dei dati oltre a questa tolleranza. Se
una release dovesse mai richiedere un rimodellamento vero di un campo esistente, `CHANGELOG.md` lo dirà esplicitamente e
darà i passi manuali.

## Quando un modulo va in pensione

Un modulo può uscire dal catalogo in una release. È successo a Firecrawl: il connettore ora è l'API cloud del fornitore,
con la tua chiave, quindi non resta niente da collocare. La derivazione ignora una voce di `modules.state.yaml` il cui
modulo non è più nel catalogo, e `nexgen doctor` la segnala una volta, come avviso, con il nome del modulo. Togli quella
riga da `03-INFRA/agent-universal-layer/modules.state.yaml` quando la vedi; nient'altro dipende da lei. Il server di un
connettore ritirato sparisce anche dalle configurazioni generate al sync successivo, e la sua chiave semplicemente non
serve più.

## Cosa non succede mai da solo

- Il tuo `VERSION` non si muove da solo.
- `nexgen sync`, `nexgen guard` e `nexgen doctor` non fanno mai `git pull` o `git checkout` sul tuo clone.
- Il tuo clone non pubblica mai codice del motore a monte. I controlli del repository GitHub e la CI valgono per i
  maintainer che pubblicano cambiamenti, non per il normale uso privato del Vault.

## Pin dei pacchetti MCP

Il motore esegue i pacchetti MCP locali con versioni `npx` esatte. I pin stanno in
`03-INFRA/agent-universal-layer/mcp/manifest.yaml`, con il bridge HTTP di Antigravity bloccato in
`nexgen_core/renderer.py`.

Non sostituire un pin con `latest`. Prova un aggiornamento di pacchetto in un setup usa e getta, esegui i controlli del
motore, poi pubblica il cambiamento del motore. Se il nuovo pacchetto causa una regressione, riporta il clone al tag
precedente come descritto sopra.

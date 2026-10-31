# Contratto di architettura

🇬🇧 [English](../architecture-contract.md)

Questo documento definisce il comportamento che ogni componente deve conservare quando la sua implementazione cambia.
Registra anche i vincoli di progetto e il debito da controllare durante un refactor. Per l'indice attuale dei moduli e
dei simboli vedi [codebase-map.md](codebase-map.md).

## 1. Gli invarianti

Ogni componente li rispetta. Un progetto che ne rompe uno è sbagliato anche se passa i suoi test.

1. **Una sola fonte canonica.** Politica, configurazione MCP, skill, memoria e identità privata esistono una volta
   sola. I file per CLI e per macchina sono generati, e niente viene modificato a mano a valle della propria fonte.
2. **Tutto è pigro per impostazione predefinita.** Una voce nuova guadagna il caricamento immediato con un argomento;
   non lo riceve per default. L'utente sa che una capacità esiste e la prende quando vuole.
3. **Il nome segue ciò su cui il comando agisce.** La memoria è il Vault, quindi i comandi sulla memoria tengono il
   prefisso `vault-`. Il motore e i suoi strumenti prendono `nexgen-`.
4. **Il motore distribuisce solo i propri comandi.** Tutto il resto resta nei dati privati. Un elenco di extra
   consigliati è documentazione, non file.
5. **Ciò che è scelto si propaga.** Ogni CLI prevista, ogni macchina dichiarata, ogni piattaforma puntata. Lavorare su
   una sola macchina non è finire.
6. **Ripara in silenzio, parla solo di ciò che non si può riparare.** La manutenzione ordinaria è il lavoro, non una
   notizia. Avvisare del lavoro ordinario è così che le persone imparano a ignorare le notifiche.
7. **Un solo megafono.** Esiste esattamente una superficie di allarme. Aggiungerle un trigger è consentito; aggiungere
   un secondo notificatore no.
8. **Configurazione e codice arrivano con orologi diversi.** I dati raggiungono ogni macchina in minuti; il codice
   arriva con una release. Ogni consumatore di un file dichiarativo deve quindi tollerare una chiave o un valore che
   non capisce, saltando quella voce in modo rumoroso, mai rifiutando il documento.
9. **Un solo scrittore, una sola definizione di segreto, un solo lettore dell'host.** Un file di proprietà del motore
   viene scritto tramite `nexgen_core.files` (atomico, conserva i permessi, con backup dove conta), e un test di
   scansione fa fallire la build su una `write_text` diretta. L'aspetto di una credenziale è definito in
   `nexgen_core.secret_shapes`, e un solo corpus di credenziali sintetiche passa attraverso ogni suo consumatore. Tutto
   ciò che cambia la macchina prende lo stesso lock dell'host, e una diagnostica che chiede "questo apply cambierebbe
   qualcosa?" lo chiede al renderer stesso in anteprima invece di ricalcolare la risposta. Anche dove vive un server MCP
   (montato direttamente, dietro il gateway o assente) è una sola funzione che il renderer, il gateway e
   `nexgen mcp plan` chiamano tutti.

## 2. Componenti e loro contratti

### L'orologio

Fa partire il ciclo ricorrente e nient'altro. Sopravvive a riavvii ed esecuzioni saltate. Non pubblica mai. Non tiene
lavoro che un altro componente può fare.

### Il ciclo di guardia

Una sola transazione: prende un lock valido su tutto l'host, scarica il remoto autorevole, classifica lo stato dei dati
e applica solo quando quello stato è sicuro. Rigenera ogni file di runtime derivato dalla sua fonte canonica. Non
pubblica mai. Rifiuta di toccare un albero di lavoro con modifiche non committate dell'utente, e nomina i file che
l'hanno bloccato senza modificarli.

### Il giudice

Decide se il livello è allineato, ed è l'unico componente autorizzato a formare quel verdetto. Riporta ciò che richiede
attenzione; un controllo superato viene contato, non raccontato. Distingue tre casi e non li confonde mai:

- qualcosa è rotto e l'utente non l'ha scelto: un **guasto**;
- qualcosa è fuori posto ma l'utente l'ha scelto: **non viene riportato affatto**;
- qualcosa non si può determinare adesso: **dichiarato come indeterminato**.

### Il megafono

L'unica via di consegna per tutto ciò che una persona deve leggere. Possiede la forma del messaggio, il debounce e i
ripieghi di trasporto. Qualsiasi numero di trigger può svegliarlo; nessuno di loro può formattare o consegnare da solo.
Un trigger deve poterlo raggiungere anche quando ciò che sorveglia è morto, perché un allarme ospitato dentro la cosa
che monitora non suona quando quella cosa non parte.

Consegna attraverso questi trasporti, in questo ordine, e solo il desktop aspetta che tutti gli altri falliscano:

1. **OpenClaw**, quando `openclaw` è nel `PATH` e una destinazione è configurata: `NEXGEN_ALERT_CHANNEL` (per esempio
   `telegram`) più `NEXGEN_ALERT_TARGET` (l'id della chat, `@username`, `channel:<id>` e così via, nella forma che
   documenta `openclaw message send`). Quando il canale è `telegram` e nessun target è impostato, `TELEGRAM_CHAT_ID` fa
   da target, così una macchina che conosce già la sua chat non lo dice due volte. L'allarme viaggia come testo
   semplice, un solo elemento argv e mai una riga di comando montata a mano, con `stdin` chiuso e un tetto di venti
   secondi. Su Windows lo shim `openclaw.cmd` di npm viene risolto in `node` più il
   `node_modules/openclaw/openclaw.mjs` del pacchetto, perché Windows avvia un file batch solo tramite `cmd.exe`, che
   rileggerebbe `&`, `|`, `^` e `%` dentro un allarme che porta messaggi di errore e nomi di file; quando quella voce o
   node stesso mancano, OpenClaw non è un trasporto su quella macchina. Un'uscita diversa da zero, un timeout o un
   binario mancante non sono un errore: prende l'allarme il trasporto successivo.
2. **Telegram diretto**, con `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`, per una macchina senza OpenClaw. Non gira mai
   dietro un allarme già consegnato da OpenClaw: dove OpenClaw possiede il canale Telegram, la sua consegna e la Bot API
   sono la stessa destinazione, e mandarle entrambe è lo stesso messaggio due volte.
3. **Un webhook**, con `AGENT_ALERT_WEBHOOK_URL`, a meno che OpenClaw abbia già portato l'allarme: un allarme raggiunge
   una sola superficie, e un webhook che ripete ciò che l'harness ha consegnato è lo stesso allarme due volte.
4. **Il desktop**, solo quando niente prima di lui ha consegnato.

### Il battito di liveness

Indipendente dalla guardia, con il proprio orario e nessuna dipendenza da lei. Risponde a una domanda: la guardia è
arrivata in fondo di recente. Un lavoro annullato da una dipendenza fallita non entra mai in uno stato di guasto, quindi
gli allarmi innescati dai guasti da soli non lo vedono; il tempo trascorso dall'ultima esecuzione completata copre quel
caso e ogni altra causa senza sapere quale. Porta anche i due compiti di manutenzione qui sotto, perché è l'unico posto
che gira regolarmente senza tenere il lock della guardia.

### L'auto-aggiornatore

Prende una release pubblicata senza chiedere e non ne parla. Rifiuta su un albero sporco, e considera solo un tag che
esiste come release pubblicata. Ha un tetto su quanto grande può essere il salto preso in automatico,
predefinito al minimo, perché una macchina che cambia comportamento durante la notte l'ha cambiato senza che nessuno lo
scegliesse. Parla solo quando non riesce a fare il lavoro, e un tentativo fallito deve nominare il recupero, non il
controllo.

Verifica il tag di release contro i firmatari bloccati nella copia già installata ([SECURITY.it.md](../../SECURITY.it.md),
"What an installed copy verifies"). Una firma sbagliata, o di una chiave non bloccata, viene rifiutata in ogni modalità.
Una release che non si riesce a verificare affatto viene rifiutata quando non c'è nessuno a leggere l'avviso. Il merge
avviene sotto il lock dell'host, e un aggiornamento automatico che fallisce dopo che il motore si è mosso
viene disfatto sul posto e ricordato come rifiutato, così non resta mezzo applicato né viene ritentato ogni ora. Il
comando interattivo non disfa mai niente da solo: c'è una persona che guarda.

### Il controllo delle dipendenze

Cerca a monte ogni cosa di terze parti bloccata che il livello dichiara: codice scaricato a un commit, pacchetti fissati
a una versione, strumenti invocati per nome e versione. Produce un elenco e si ferma lì, perché applicare un cambiamento
a monte altera un comportamento che nessuno ha scelto. Non notifica mai. Essere offline non scrive niente e non riporta
niente: una postazione è offline di continuo e non è un incidente.

Riporta anche quando il publisher ha ritirato il supporto per la versione esatta bloccata (`deprecated` di npm), perché
"non esiste niente di più nuovo" non è lo stesso di "va tutto bene". Sorveglia ciò che un modulo dichiara di portare
(`upstream:` nel catalogo dei moduli): un programma distribuito come immagine Docker (n8n, l'API OCR del Vault), o una
versione bloccata dentro uno script launcher (Playwright). Un modulo dice quale file tiene il pin e dove si legge la
release più nuova, così il controllo non deve saperlo. Questi vengono solo riportati, e il guardiano li trattiene tutti:
un programma si sostituisce dove gira, quell'host può bloccare altro, e il launcher di Playwright modifica i file di una
versione esatta, quindi anche un salto di patch non è un piccolo ritocco.

### Il materializzatore di skill

Trasforma una dichiarazione nelle viste che ogni runtime sa vedere. Quattro origini, e la distinzione riguarda chi
possiede i byte:

- **di proprietà dell'utente**: portata nei suoi dati;
- **di proprietà del prodotto**: letta dal motore installato, mai copiata nei dati dell'utente, così un aggiornamento
  aggiorna il comando e nessuna seconda copia può diventare vecchia;
- **di terze parti, scaricabile**: bloccata a un commit immutabile e ripristinata da lì;
- **di terze parti, che solo il suo installer sa rendere**: bloccata a una versione, installata da quell'installer
  quando la copia locale non corrisponde.

Materializza in una libreria non scoperta, poi crea solo le viste dichiarate. Un installer che lascia la sua copia in
una radice di scoperta se la vede spostare; ricordarsi di spostarla non è un meccanismo.

### Il renderer di runtime

Genera la configurazione di ogni CLI dal manifest dei connettori. Omette un connettore la cui precondizione dichiarata
non è soddisfatta, senza trattare quell'omissione come un guasto. Conserva le impostazioni locali e specifiche della
macchina che non possiede.

### Il distributore di credenziali

Scarica le chiavi operative su richiesta dal backend privato e le scrive con permessi restrittivi. I segreti non
passano mai dal controllo di versione, non compaiono mai nei log e non compaiono mai in un riepilogo.

### La superficie dell'identità

Impedisce che la presentazione del runtime diventi l'identità privata: neutralizza i controlli di personalità
supportati, disattiva le memorie native parallele, mette in quarantena quelle esistenti senza cancellarle. Classifica
ciò che trova: una violazione di confine blocca, un difetto di metadati avvisa. Una guardia che blocca per un problema
di formattazione manda giù tutto il livello per una riga mancante, ed è quello che è successo.

### Il publisher

Committa e pubblica il lavoro durevole in un'unica operazione. Mette in stage solo ciò che gli è stato dato. Non inventa
mai un commit da un albero sporco.

## 3. I contratti dei dati

Due file dichiarativi sono tutta la superficie di configurazione. Entrambi devono essere compatibili in avanti secondo
l'invariante 8.

**Manifest dei connettori.** Una voce per server: come avviarlo, quali runtime lo montano, la precondizione che lo
governa e se è fondamentale o facoltativo. Facoltativo è il valore predefinito quando non è dichiarato, così niente si
promuove da solo.

**Manifest delle skill.** Una voce per skill: chi possiede i byte (sopra), quali runtime ricevono una vista nativa e se
è immediata o pigra. Pigra è il valore predefinito.

**I file di stato** sono locali alla macchina, non si sincronizzano mai, e ognuno risponde a una sola domanda.
Condividere un file tra due domande è ciò che ha congelato la liveness dietro un debounce di allarme.

## 4. La superficie dei comandi

Raggruppata per ciò che l'utente sta davvero chiedendo.

- **Allinea questa macchina adesso** e **allineala secondo un orario**: la stessa transazione, una manuale e una
  ricorrente.
- **Pubblica il mio lavoro.**
- **Dimmi se qualcosa non va**, con un rapporto predefinito che mostra solo quello, e una forma dettagliata che elenca
  tutto ciò che è stato controllato. `--summary` è la forma che usano un hook git e una raccolta: interroga i controlli
  indipendenti nello stesso momento, smette di aspettare dopo sette secondi e riporta ogni controllo che non ha
  risposto come indeterminato, con l'azione che porta a risolverlo, invece di scartarlo. Il doctor completo aspetta ogni
  controllo, e `--timings` dice quale ha speso il tempo.
- **Aggiorna il motore**, in modo interattivo con un cancello di conferma.
- **Mostrami cosa si è mosso a monte.**
- **Comandi sulla memoria**: salva un fatto, chiudi una sessione in note, consolida le note, mappa la struttura delle
  note.
- **Trova e mostra una skill su richiesta**, che è ciò che rende usabile il caricamento pigro.
- **Convoca una revisione tra fornitori diversi.**

## 5. Lezioni dalle implementazioni precedenti

Questi guasti hanno dato forma ai contratti qui sopra. Descrivono implementazioni precedenti; controlla il codice
attuale e il backlog prima di trattarne uno come difetto aperto.

1. **Logica di piattaforma duplicata:** implementazioni del doctor per Linux e Windows separate sono andate alla deriva.
   Ora il core Python condiviso possiede la diagnostica, e i launcher di piattaforma inoltrano gli argomenti.
2. **Logica dentro la shell:** un grande script del doctor mescolava shell e interpreti in linea. Tieni i controlli in
   moduli verificabili in modo indipendente.
3. **Report controllati da variabili globali:** la stessa chiamata stampava o contava in silenzio a seconda dei flag.
   Restituisci esiti strutturati e scegli la formattazione al confine del comando.
4. **Test legati ai dettagli implementativi:** conteggi fissi di attività e stringhe di output esatte bloccavano
   cambiamenti validi. Verifica regole osservabili, come i percorsi delle attività e la segnalazione dei guasti.
5. **Valori predefiniti cambiati senza compatibilità:** i consumatori dipendevano dal vecchio output di un comando.
   Tratta i cambiamenti di output come cambiamenti di contratto.
6. **Affermazioni di sicurezza non supportate:** la documentazione dichiarava un controllo delle firme lato client
   mentre il client avvisava e proseguiva. Verifica le affermazioni di sicurezza sia contro il workflow di release sia
   contro l'updater.
7. **Lettori che rifiutavano la configurazione nuova:** una chiave sconosciuta poteva fermare un consumatore più
   vecchio. Conserva la regola di compatibilità in avanti dell'invariante 8.

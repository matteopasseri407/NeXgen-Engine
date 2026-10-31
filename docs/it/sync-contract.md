# Contratto della transazione di sync

🇬🇧 [English](../sync-contract.md)

In un'installazione MULTI, `nexgen sync` e `nexgen guard` (nome storico `agent-sync`) trattano la propagazione come
un'unica transazione protetta. Non rigenerano mai i file delle CLI solo perché è stato tentato un pull: prima lo stato
autorevole dei dati deve risultare sicuro.

## Proprietà dei remoti

Il Vault dei dati possiede la politica dei remoti in:

```text
03-INFRA/agent-universal-layer/sync/remotes.yaml
```

Parti da `remotes.yaml.example`. `authoritative_remote` è l'unico remoto usato per decidere se i dati locali sono
freschi, avanti, sporchi o divergenti. Le voci sotto `mirrors` sono copie di pubblicazione: uno specchio vecchio o non
disponibile produce un avviso, ma non sostituisce mai la storia autorevole.

`KNOWLEDGE_VAULT_REMOTE` e `KNOWLEDGE_VAULT_MIRRORS` formano una sostituzione d'emergenza completa. Senza file e senza
sostituzione, il valore predefinito portabile è `origin` senza specchi. Una configurazione non valida si ferma prima che
il provisioner crei file di runtime. Ispeziona i valori risolti con:

```bash
nexgen config authoritative_remote
nexgen config mirrors
```

(Nome storico: `agent-sync config authoritative_remote|mirrors`.)

## Comandi

I nomi storici (`agent-sync <mode>`) funzionano ancora come alias; la tabella dà i nomi principali `nexgen`.

| Comando | Contratto |
|---|---|
| `nexgen guard` | Pull, apply e healthcheck ricorrenti. Non pubblica mai. Un lock occupato è un salto sicuro. |
| `nexgen sync` (alias `nexgen apply`) | Transazione manuale di pull e apply. Non pubblica mai. |
| `nexgen sync --dry-run` / `nexgen plan` | Il piano: cosa cambierebbe un apply, calcolato senza scrivere, senza scaricare e senza materializzare segreti. Espone `DRIFT` ed esce sempre con 0. |
| `nexgen sync --check` / `nexgen plan --check` | Lo stesso piano, che esce con codice diverso da zero quando trova deriva (cancello CI). `nexgen plan --json` scarica il piano con la provenienza. |
| `nexgen pull` | Solo pull e healthcheck. Non rigenera mai i file delle CLI. |
| `nexgen publish` (alias `nexgen vault push`) | Pubblica i commit esistenti sul remoto autorevole, poi sugli specchi configurati. Non fa mai pull né apply. |
| `nexgen preflight` | Valida il contratto di configurazione locale senza scaricare né generare file di runtime. |
| `nexgen doctor` | Esegue solo diagnostica e allarmi. |
| `nexgen bootstrap-alerts` | Esegue diagnostica e allarmi solo su FAIL. Comando interno, usato dai timer. |

## Il piano (anteprima senza effetti collaterali)

`nexgen plan` è l'unico oggetto piano condiviso da anteprima e dump. Sonda la deriva con i controlli in sola lettura del
doctor stesso (stato del render MCP, skill dichiarate, viste iniziali, puntatori alle istruzioni) più una sonda Git solo
locale (conflitti, ramo, lavoro non committato). Due limiti sono dichiarati nell'output del piano invece di restare
nascosti:

- niente rete. Lo stato avanti e indietro rispetto al remoto non si può sapere senza un fetch, e un piano non scarica
  mai, quindi anteprima e apply non sono perfettamente equivalenti per la parte Git che guarda a monte;
- sonde, non simulazione. Le fasi di autoriparazione idempotenti (shim, scheduler, moduli) sono dichiarate, non
  enumerate. Su una macchina allineata non scrivono niente.

`--check` trasforma la deriva in un'uscita diversa da zero per la CI; `--json` emette il piano con la provenienza
(versione del motore, Vault, ramo, commit, riscontri per dominio). Il piano non esegue mai rimedi, non scrive mai e non
tocca mai la rete.

Quattro regole tengono onesto il piano:

- una sonda che non può girare viene riportata come deriva, perché un apply fallirebbe allo stesso modo;
- una sonda indeterminata viene dichiarata sotto "not checked here" invece di sparire;
- un avviso non è mai "allineato": un avviso che un apply riparerebbe è deriva, e uno che aspetta l'utente finisce sotto
  "needs attention";
- un file di configurazione assente per una CLI che il manifest si aspetta di configurare viene dichiarato come azione
  prevista (`render would create …`), non come luce verde.

Eseguire `nexgen` senza comando stampa l'aiuto e non cambia niente. `sync`, `guard`, `pull` e `preflight` sono comandi
separati ed espliciti, così un errore di battitura o un argomento dimenticato non può combinare pull, modifica del
runtime, lavoro sulle credenziali e pubblicazione.

## Cancello di freschezza

L'apply è consentito solo quando il ramo locale corrisponde a quello autorevole, gli è appena andato avanti in
fast-forward, oppure è dichiarato esplicitamente come solo locale. È bloccato quando l'albero tracciato è sporco, il
remoto manca, il fetch fallisce, il ramo atteso non è agganciato, il ramo locale è avanti, le storie divergono, o Git
non riesce a dimostrarne lo stato.

Solo per un'interruzione di rete esiste un recupero manuale deliberato:

```bash
nexgen sync --allow-offline
```

`nexgen guard --allow-offline` accetta la stessa sostituzione. Non aggira mai gli stati sporco, avanti o divergente, solo
un remoto mancante o un fetch fallito.

## Cancello di configurazione

Prima che i file di runtime generati vengano scritti, `guard` e `sync` eseguono lo stesso preflight di
`nexgen preflight`. Carica il manifest MCP versionato (`mcp/manifest.yaml`) e il manifest delle skill
(`skills.manifest.yaml`) quando ci sono, e fa fallire la transazione se uno dei due non è ben formato: non è una mappa
YAML, oppure manca la sua sezione di primo livello obbligatoria.

### Scrivere il manifest: `nexgen mcp add`

Il manifest resta l'unica dichiarazione canonica. Le modifiche a mano restano consentite; `nexgen mcp add <name>
--targets <clis|all>` è la via supportata per scriverne una senza comporre YAML a mano:

- validato prima di ogni scrittura: chiave del manifest sicura, esattamente uno tra `--command` e `--url`, URL http(s),
  stringhe su una sola riga, autenticazione solo tramite nome di variabile d'ambiente, valori `--env` con forma di
  segreto rifiutati con la forma `${VAR}`, e un nome di variabile sensibile con un valore in chiaro rifiutato allo stesso
  modo. Le combinazioni di flag che verrebbero scartate in silenzio, come `--auth-env` con `--command` o `--env` con
  `--url`, sono errori;
- scritto attraverso lo stesso percorso bloccato e atomico di `--adopt --apply`: un lock dedicato dell'host serializza
  gli scrittori, la sostituzione è un file temporaneo più rename così un crash non tronca mai il manifest, il risultato
  viene rivalidato ricaricandolo, e ogni guasto torna atomicamente all'originale;
- un nome già dichiarato, o elencato sotto `retired_servers`, viene rifiutato;
- le voci si montano per impostazione predefinita (`tier: core`). `--lazy` le instrada invece attraverso il proxy, e al
  momento dell'aggiunta non viene reso niente: il sync o la guardia successivi rigenerano le CLI dal manifest, come per
  le modifiche a mano.

### Template in linea per sistema operativo

I valori stringa del manifest possono portare `{{ .os }}`, `{{ .home }}`, `{{ .vault }}`, `{{ .engine }}` e
`{{ if eq .os "windows" }}…{{ else }}…{{ end }}`, così una sola dichiarazione copre macchine Linux e Windows senza
duplicare una voce in un blocco `windows:` quando cambia un solo percorso. Quella sostituzione resta valida per cambi di
voce intera. Il renderer e il proxy pigro espandono lo stesso dialetto; l'espansione avviene prima di quella di
`${VAR}`, e una variabile sconosciuta o un blocco malformato fa fallire il render invece di indovinare.

Entrambi i manifest portano `schema_version: 1` e vengono letti con tolleranza secondo l'invariante di compatibilità in
avanti: una singola voce di connettore o di skill malformata, o priva di un campo obbligatorio, viene saltata con un
avviso invece di fermare l'intero documento. Solo un manifest strutturalmente rotto ferma la transazione, cioè quando il
documento stesso non è una mappa o la sua chiave obbligatoria di primo livello è del tipo sbagliato. Il file delle
sedute del Council (`seats.yaml`) viene caricato con la stessa tolleranza, ma solo quando gira il comando `council`; non
fa parte del preflight di `sync` e `guard`.

Questo rende un manifest strutturalmente non valido una condizione di stop prima che il motore cambi la configurazione
di una CLI. Il comando di preflight scrive solo il suo lock e il suo registro di esecuzione.

## Lock ed esito

Un lock valido su tutto l'host copre l'intera operazione. Un'esecuzione aspetta fino a 30 secondi per prenderlo, abbastanza
da superare un ciclo normale, così il timer ricorrente che incontra un'esecuzione interattiva aspetta invece di
segnalare la macchina occupata. Si cambia con `AGENT_SYNC_LOCK_TIMEOUT_SECONDS`. Una contesa manuale oltre quella
finestra esce con codice `75`; la contesa della `guard` ricorrente esce con successo, perché l'esecuzione attiva possiede
già il lavoro. Ogni fase dichiarata riporta successo o guasto. I guasti vengono aggregati, i controlli indipendenti
successivi girano comunque, e il codice di uscita finale è diverso da zero se una fase obbligatoria è fallita.

Una transazione `sync` o `apply` riuscita termina con il messaggio "Alignment completed successfully" (`Allineamento
completato` in italiano). Controllare se il risultato è davvero pulito è un passo separato: esegui
`nexgen doctor --strict --summary` dopo `sync`, perché `sync` stesso non esegue il doctor strict né classifica un
verdetto di prontezza.

La logica di pubblicazione (`nexgen vault push`, nome storico `vault-push`) è la classe `Publisher` di
`nexgen_core/publisher.py`, non un'implementazione separata. Blocca lo stesso file di lock per impostazione predefinita
(`AGENT_SYNC_LOCK_FILE`, altrimenti `agent-sync.lock` nella directory di stato di questo host), così un ciclo `guard` e
un `nexgen vault push` manuale sulla stessa macchina si serializzano comunque.

I launcher Linux e Windows chiamano la stessa implementazione Python. I test automatici coprono entrambi i dialetti di
percorso e il codice di lock Windows, ma un cambiamento di architettura non è operativamente completo finché non è stato
eseguito anche su un'installazione Windows fisica.

## Ordine dei livelli di configurazione

La configurazione MCP che ogni CLI riceve è il risultato di una pipeline fissa e ordinata dentro
`nexgen_core/renderer.py`. L'ordine qui sotto è il contratto: è ciò contro cui vengono confrontate le configurazioni
generate, e un test di regressione lo blocca. Più tardi gira un livello, più vince; un campo che un livello successivo
non imposta resta come l'ha prodotto quello precedente.

1. **Manifest canonico** (`mcp/manifest.yaml` nella radice dei dati del Vault), l'unica fonte autorevole dei server. Un
   server non dichiarato qui non ha una fonte portabile e viene riportato dal doctor come fuori manifest, solo WARN.
2. **Sostituzione per sistema operativo** (un blocco `windows:` dentro la voce di un server), applicata solo su Windows e
   poi scartata dalla vista fusa. È così che un solo manifest serve entrambi i sistemi senza indovinare.
3. **Espansione dei segnaposto di percorso** (`${AGENT_ENGINE_ROOT}`, `${AGENT_VAULT_DATA}`, `${KNOWLEDGE_VAULT_PATH}`),
   risolti contro le radici reali di motore e dati dell'host.
4. **Normalizzazione degli shim Windows**: i nomi comuni dei wrapper di interprete (`npx` in `npx.cmd`, `node` in
   `node.exe`, `python3` in `python`) risolti in percorsi reali, e gli shim `.cmd` o `.bat` instradati attraverso
   `cmd.exe` così ogni client può avviarli.
5. **Espansione dei segnaposto di ambiente** (`${VAR}` o `${VAR:-default}`), espansi dall'ambiente dell'host. Un server
   governato da `require_env` viene omesso del tutto quando la sua variabile manca.
6. **Conservazione additiva della configurazione viva**: i campi che la configurazione di una CLI porta su un server
   gestito ma che il manifest non dichiara, come metadati di runtime e sovrapposizioni lato client, vengono conservati in
   modo additivo dagli scrittori chirurgici. La fusione è superficiale di proposito: `env` è un'unica unità dichiarata
   dal manifest, quindi il blocco env del manifest vince nel suo insieme e un'aggiunta di env lato client non viene
   portata nella configurazione generata. Il client applica la propria sovrapposizione di env a runtime.

Invariante: il percorso di render non scrive mai il manifest canonico, tranne attraverso il flusso esplicito `--adopt`
con backup e rivalidazione. L'ultimo livello a vincere è la conservazione additiva dal vivo, mai il contrario.

## Limite noto

Tutto questo contratto è costruito per una persona che tiene allineate diverse macchine proprie, non per un team che
scrive nello stesso Vault nello stesso momento. Il lock descritto sopra è per macchina, non per proprietario: è un lock
di file locale sotto la home di quella macchina, e serializza solo processi concorrenti su quella stessa macchina, per
esempio un ciclo `guard` che si sovrappone a un `nexgen vault push` manuale. Non fa nulla per arbitrare tra le macchine
di un solo proprietario che girano insieme, tantomeno tra le macchine di persone diverse contro lo stesso Vault.

Gli scrittori concorrenti sono protetti invece dal push atomico di Git: un push non fast-forward viene rifiutato dal
remoto, mai sovrascritto in silenzio, e il percorso di pubblicazione scarica, confronta e poi o riprova con un rebase
pulito o si ferma e chiede una risoluzione manuale su un conflitto vero. Resta un vuoto: due macchine che modificano
parti diverse e non in conflitto dello stesso file quasi nello stesso momento possono essere fuse da quel rebase
automatico senza alcun avviso ai due proprietari. Non si perde niente, ma il merge non viene mai riletto. Se un team
condivide un Vault come infrastruttura comune, le scritture concorrenti di più persone non sono uno scenario provato né
supportato: aspettati normali conflitti di merge Git, senza strumenti aggiuntivi in questo contratto per risolverli. Vedi
[team.md](team.md) per capire perché un Vault condiviso è già un problema di adattamento mono-utente prima ancora che il
sync entri in gioco.

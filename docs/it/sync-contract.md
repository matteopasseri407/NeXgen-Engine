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

## Claude Desktop (Chat e Cowork)

L'app desktop è una destinazione del renderer con un contratto suo: `nexgen_core/mcp_render/claude_desktop.py` possiede
la tabella `mcpServers` di `claude_desktop_config.json` e nient'altro, perché il resto di quel file (preferenze, stato
delle finestre) appartiene all'app, che può salvarlo in qualsiasi momento.

**Cosa ricevono Chat e Cowork per impostazione predefinita: niente.** Nessun server arriva all'app finché la sua voce
nel manifest non imposta `desktop_allow: true`, e il modello distribuito non dichiara quel flag su nessun server
(`03-INFRA/agent-universal-layer/mcp/manifest.yaml`). Decidono due filtri, e il trasporto decide solo come viene montata
una voce che sopravvive: il flag (assente o non esattamente `true` significa nega), poi il gateway (rifiutato anche con il
flag, perché tutto ciò che sta dietro diventerebbe raggiungibile, compresi i server che scrivono). Una voce stdio viene
scritta col suo comando; una remota passa dal `mcp-http-bridge.mjs` del motore, la stessa forma
`node <bridge> <url> <variabile del token> mcp-remote@0.1.38` che riceve Antigravity, quindi l'app riceve l'URL e il NOME
della variabile del bearer, mai il suo valore.

**Perché il default nega.** L'app non ha ganci di guardia: nessun file di permessi, nessun hook, nessuna sandbox
davanti a una chiamata a un tool, quindi l'unica cosa tra un documento non fidato e l'host è il popup Allow dell'app. Il
set di strumenti che l'app erediterebbe comprende scritture nel Vault, mutazione di workflow e codice di browser
arbitrario. Il Vault è l'unico permesso che il proprietario ha comunque deciso, e la sezione successiva dice perché.

**Come si ritira un permesso.** Il renderer tiene un file di servizio (`claude_desktop_config.managed.json`) con i nomi
che ha scritto lui. Un nome che perde il flag viene rimosso al ciclo successivo. Una voce aggiunta a mano nell'app non
viene mai toccata, e `mcp.desktop_allowlist` in `nexgen doctor` segnala quelle note al manifest come WARN invece di
cancellarle.

### Come attivare il Vault, in lettura e in scrittura

La regola per questa app è stata concedere un server solo se è di sola lettura, perché l'app non ha ganci di guardia.
Per il Vault la decisione del proprietario è lettura E scrittura, e il motivo sta nel contratto del server, non in
questo: una scrittura costruita su una lettura vecchia viene rifiutata invece che applicata (`update_note` e
`update_section` accettano `expected_hash`, e `create_note` rifiuta di sovrascrivere), ogni scrittura è un commit Git,
quindi si può annullare, e il server non espone nessun tool di cancellazione. La convenzione della sola lettura non vale
per lui.

Quindi il Vault in Chat e Cowork è la voce `vault-library` distribuita più un flag:

```yaml
  vault-library:
    # ... i campi distribuiti (transport: http, url, auth, require_env) ...
    desktop_allow: true
```

Poi lancia `nexgen sync` (o lascia che ci arrivi `guard`) e riavvia l'app. `vault-library` è `transport: http`, quindi il
renderer lo monta dal `mcp-http-bridge.mjs`, lo stesso bridge che usa Antigravity. La voce generata è

```json
{"command": "node", "args": ["<engine>/agent-universal-layer/mcp/mcp-http-bridge.mjs",
                             "https://<host del Vault>/mcp", "VAULT_LIBRARY_TOKEN", "mcp-remote@0.1.38"], "env": {}}
```

Quattro cose da sapere prima di farlo:

- l'URL viene scritto già risolto, perché il render gira solo mentre `VAULT_LIBRARY_URL` è impostata (`require_env`). Il
  token non viene scritto affatto: il terzo argomento è il NOME della variabile che lo contiene, e il bridge legge il
  bearer da quella variabile nell'ambiente del processo che l'app avvia. Quindi `VAULT_LIBRARY_TOKEN` deve arrivare
  all'app dal suo ambiente (`~/.config/environment.d` o il deposito dei segreti su Linux, l'ambiente utente su Windows):
  l'app parte da una sessione grafica, non da una shell;
- `exposure` e `targets` non valgono per questa app, vale solo il flag, quindi un server che il piano instrada dietro il
  gateway qui viene montato direttamente quando è flaggato, e in quel caso `readonly` e `readonly_tools` non proteggono
  nulla;
- `tools_allow` resta l'opzione per un server che deve davvero essere di sola lettura, qualunque trasporto parli: il
  renderer scrive allora `mcp-trim.py <nome>` come comando, e lo shim elenca solo i tool permessi e rifiuta la chiamata a
  qualsiasi altro. Una voce così non passa dal bridge, perché il processo figlio è il wrapper e parla HTTP lui al server
  vero, con il bearer preso dalla catena delle credenziali. `readonly` e `readonly_tools` non sono quel limite: decidono
  se `lazy_call` o `lazy_mutate` inoltra un tool, il che ha senso solo dietro il gateway, e il gateway non si monta qui;
- la forma del bridge su questa app è fissata in `test_nexgen_claude_desktop.py`: un argv letterale, gli stessi argomenti
  che Antigravity scrive per la stessa voce, il valore del token assente dal file, una voce HTTP senza flag assente, e la
  guardia che conserva una voce del bridge il cui `require_env` non vede.

Se l'obiettivo è una macchina con il Vault in sola lettura per tutti, l'altra leva sta sul server, non in questo
contratto: `VAULT_WRITE_ENABLED=false` fa servire il `vault-library` distribuito come `markdown-vault-readonly` e non
espone affatto i tool di scrittura. Vale per istanza e per ogni client che la usa, compreso il percorso di scrittura del
proprietario.

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

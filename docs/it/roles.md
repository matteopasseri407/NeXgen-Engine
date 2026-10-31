# Ruoli della macchina: quale comando lanciare

🇬🇧 [English](../roles.md)

NeXgen Engine ha tre ruoli di macchina.
Ognuno è idempotente: l'installer porta la macchina da zero a pronta con un comando solo, e puoi rilanciarlo quando vuoi.

## 1. Server Linux headless (`server`)

Un server Linux sempre acceso, un home lab, un VPS o un'istanza cloud senza monitor.

```bash
sh install.sh --role server
```

Cosa fa:

- Richiede Python 3.11+ e il pacchetto venv corrispondente (`sudo apt install python3-venv` su Ubuntu 24.04, oppure `python3.X-venv`): il motore costruisce il proprio ambiente con quello, e senza di lui ogni allineamento fallisce con `No module named pip`.
  Verifica i requisiti di sistema e recupera Python 3.11+ tramite `uv` in modalità utente quando manca.
- Rifiuta configurazioni headless non sicure, come `sudo` senza password o un utente nel gruppo `docker`.
- Scrive la policy sudo degli agenti in `/etc/sudoers.d/nexgen-agents`: quindici comandi nominati (log e riavvii dei container, il file compose nella tua home, lo stato `systemctl` e i due riavvii, `journalctl`, `apt`), mai root, mai più larga di quella lista.
  `visudo -cf` legge il file prima che accada qualcosa di privilegiato.
  Senza `sudo` senza password stampa il file e l'unico comando `sudo install`, perché un'installazione non presidiata che si ferma per una password è un'installazione che si blocca.
- Attiva il lingering utente di systemd (`loginctl enable-linger`).
- Configura il gateway OpenClaw sul loopback (`127.0.0.1:18789`) e lo espone sulla tua tailnet tramite Tailscale Serve su HTTPS. Nessuna porta esterna viene aperta.
- Irrobustisce i permessi della cartella di stato (`chmod 0700`, e azzera la scrittura per gruppo e per tutti) e deposita e verifica l'entrypoint del plugin del motore `nexgen`.
- Genera o aggiorna `$OPENCLAW_STATE_DIR/.env` (`~/.openclaw/.env`, permessi `0600`), conservando le chiavi già definite dall'utente e aggiungendo solo le variabili mancanti citate dalla configurazione e dai server MCP montati.
  Non registra mai i valori dei segreti.
- Avvia il gateway come servizio utente in background.
- Applica la configurazione della squadra (`squad.yaml`) e registra le chiavi API dei provider tramite standard input.
- Tratta il proprio checkout del Vault come di sola lettura: il ruolo è registrato sulla macchina, `nexgen pull` fa solo fast-forward di quel clone, quello che c'è in locale finisce su un ramo `quarantine/diverged-*`, e `vault-push` rifiuta di pubblicare da lì.
  Le note si scrivono con `vault-library`, che possiede il worktree che pubblica.
- Registra il ruolo nello stato della macchina appena il setup configura il gateway. `nexgen role` lo stampa, e una macchina già in servizio lo registra con `nexgen role set server` invece di reinstallare.

Quando entri in questa macchina come utente del gateway, entra anche nella sua home (`sudo -iu <user>`, oppure `cd` prima di lanciare qualcosa che parla con OpenClaw).
OpenClaw avvia un piccolo helper con la cartella da cui è stato chiamato, e una cartella che quell'utente non può leggere lo fa fallire con `spawn <node> EACCES`, che sembra un Node rotto ed è solo una cartella di lavoro sbagliata.

Quella sessione `sudo -iu` non ha `XDG_RUNTIME_DIR` né `DBUS_SESSION_BUS_ADDRESS`, anche con il lingering attivo, quindi `systemctl --user` non raggiunge il bus utente.
L'installer se ne accorge, e quando `/run/user/<uid>/bus` esiste punta la run su quel bus invece di fermarsi al passo 1/9.
Se il messaggio compare lo stesso, controlla il bus con `ls -l /run/user/$(id -u)/bus` e che `loginctl enable-linger <user>` sia stato eseguito.

Aggiungi `--check` per vedere il piano di questo ruolo senza scrivere nulla: nessun launcher, nessun file sudoers, nessun gateway.

## 2. PC satellite o portatile (`node`)

Un desktop, un portatile o un PC di lavoro che delega l'orchestrazione centrale al gateway del server e si collega come nodo di esecuzione.

Prima crea un obiettivo di aggancio monouso sull'host del gateway:

```bash
openclaw devices join-code
```

Poi lancia l'installer sulla macchina nodo.
Su Linux e macOS:

```bash
sh install.sh --role node --target <join-code-or-url>
```

Su Windows:

```powershell
.\install.ps1 -Role node -Target <join-code-or-url>
```

Cosa fa:

- Installa la release OpenClaw fissata prima che qualcosa chieda a questo PC se è un nodo: la stessa release e lo stesso controllo di Node che esegue `nexgen openclaw setup` (`03-INFRA/agent-universal-layer/openclaw/pin.json`).
  Un PC con una versione più vecchia o diversa viene riallineato al pin, e un Node fuori dall'intervallo del pin ferma il ruolo con il messaggio del setup invece di lasciare una macchina a metà.
- Configura il profilo della macchina locale come `MINIMAL` (nodo secondario).
- Sincronizza gli harness di sviluppo locali (Claude Code, Codex, OpenCode, Antigravity), le skill e i connettori MCP.
- Collega l'host nodo come servizio del sistema con `openclaw connect --service --session-host --target-file <path>`, che tiene le credenziali di aggancio monouso fuori dall'ispezione degli argomenti di processo.
- Sonda la connessione in produzione con `openclaw node status --json`, riportando l'avanzamento ogni pochi secondi.
- Allo scadere del tempo stampa una diagnosi con i comandi di recupero documentati (`openclaw node status`, `openclaw node restart`, `openclaw node run --session-host`, oppure un codice nuovo da `openclaw devices join-code` sul gateway).
- Indica all'operatore la voce esatta da dichiarare nel `squad.yaml` centrale sul server (`tools.exec: host: node, node: <name>`), così le definizioni degli agenti restano centralizzate senza mutazioni RPC dai nodi.
- Con `--exec-policy full` (`-ExecPolicy full` su Windows), allinea anche le approvazioni exec di questo PC.

### Perché il ruolo node può allineare le sue approvazioni exec

Un agente che esegue i suoi comandi su un nodo li manda a quel PC, e quel PC applica le proprie approvazioni prima che qualcosa parta.
La sua policy non è quella del gateway: è il documento delle approvazioni sull'host nodo, e il risultato effettivo è il più restrittivo dei due.
Un nodo è di norma una macchina con nessuno seduto davanti, quindi una richiesta di approvazione che nasce lì può essere risolta solo dal fallback, il cui valore predefinito documentato è `deny`.
Il comando allora fallisce con `exec denied: request-failed`, che non dice nulla sulla causa.

`--exec-policy full` scrive in locale sul nodo il corpo che OpenClaw documenta per un host che non deve mai fermarsi su un'approvazione, con `openclaw approvals set --stdin`:

```json
{"version": 1, "defaults": {"security": "full", "ask": "off", "askFallback": "full"}}
```

Senza il flag non cambia nulla: quelle approvazioni appartengono alla persona, e un nodo le cui approvazioni devono restare severe le tiene.
`nexgen openclaw doctor --nodes`, eseguito sul gateway, le legge invece di scriverle e avvisa quando il nodo è più severo di quanto un agente chiede.

### Hai già NeXgen su questo PC?

Se questo computer ha già NeXgen installato, per esempio come standalone o con un gateway OpenClaw locale:

1. **Prima guarda, poi tocca (`--check` / `-Check`).**
   Su Linux e macOS:

   ```bash
   sh install.sh --role node --check
   ```

   Su Windows (PowerShell):

   ```powershell
   .\install.ps1 -Role node -Check
   ```

   Stampa il piano dettagliato riga per riga senza scrivere nulla: la versione del motore attuale e quella in arrivo, se esistono un gateway o attività pianificate, lo stato del nodo, cosa verrà fermato e cosa resterà com'è.
   Non installa OpenClaw: la release fissata viene installata al primo `--role node` vero, senza `--check`.

2. **Passaggio a nodo.**
   Crea un codice di aggancio monouso sul server (`openclaw devices join-code`), poi esegui sul PC:
   Su Linux e macOS:

   ```bash
   sh install.sh --role node --join <join-url>
   ```

   Su Windows (PowerShell):

   ```powershell
   .\install.ps1 -Role node -Join <join-url>
   ```

   In alternativa puoi impostare la variabile d'ambiente `NEXGEN_JOIN_URL`.

Cosa aspettarsi:

- **Aggiornamento del motore:** il PC viene prima portato all'ultima versione del motore dal canale esistente ([docs/upgrade.md](upgrade.md)), poi sincronizzato. Non esiste mai una seconda copia del motore accanto alla prima.
- **Gateway locale fermato in sicurezza:** un gateway OpenClaw locale attivo viene fermato e disattivato all'avvio con i comandi documentati (`openclaw gateway stop` e disinstallazione del servizio; le vecchie attività pianificate di Windows o i file `.vbs` del gateway vengono rimossi).
- **Stato conservato intatto:** la cartella `~/.openclaw` (configurazione, cronologia delle sessioni, memoria), gli agenti, le skill e gli MCP restano come sono. Nessuna cartella viene cancellata e nessun comando distruttivo viene eseguito.
- **Rilanciabile e reversibile:** rilanciato con lo stesso gateway, rileva che il PC è già un nodo (riavviando il servizio con `openclaw node start` se è fermo) ed esegue solo il sync.
  Per tornare standalone con il gateway locale di nuovo attivo, esegui `sh install.sh --role standalone` oppure `.\install.ps1 -Role standalone`.
- **Riepilogo finale:** una schermata con il gateway collegato, il nome del nodo, i servizi fermati, i dati conservati e il frammento da aggiungere al `squad.yaml` sul server.

## 3. PC standalone (`standalone`)

Un desktop o un portatile autonomo che gestisce il proprio gateway OpenClaw locale e i propri assistenti.

Su Linux e macOS:

```bash
sh install.sh --role standalone
```

Su Windows:

```powershell
.\install.ps1 -Role standalone
```

Cosa fa:

- Prepara il profilo utente locale, con `openclaw` nel registro delle CLI installate accanto a Claude Code, Codex, OpenCode e Antigravity.
- Predispone il gateway OpenClaw locale e gli strumenti di sviluppo.
- Irrobustisce i permessi della cartella di stato, verifica il deposito del plugin e sincronizza skill e server MCP.
- Aggiorna `~/.openclaw/.env` (0600) per le chiavi dei provider citate, senza sovrascrivere le impostazioni esistenti.
- Conserva i nomi degli agenti che hai scelto e applica `squad.yaml` quando c'è.

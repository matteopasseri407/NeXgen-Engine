# Sicurezza

🇬🇧 [English](SECURITY.md)

## Segnalare una vulnerabilità

Apri un security advisory privato dalla scheda Security di questo repository, oppure scrivi all'indirizzo che trovi sul profilo GitHub di chi mantiene il progetto.
Usa il canale privato quando ci sono credenziali esposte o un exploit attivo.
Per gli altri bug va bene una issue pubblica.

## Cosa non deve mai finire in un commit

- Qualunque cosa sotto `99-SECRETS/`, tranne `README.md`, `.gitkeep` e `secrets-registry.md`. Il `.gitignore` blocca già il resto, quindi controlla prima di aggiungere un file di quella cartella con `--force`.
- Chiavi API vere, token, chiavi SSH, segreti dei webhook o credenziali dei tunnel in qualsiasi file, compresi i manifest MCP, i file `.env` e le configurazioni di esempio. I file che corrispondono a `03-INFRA/deploy/*/.env.example` contengono solo segnaposto.
- Dati personali tuoi o di terzi: nomi reali legati a un contesto privato, ID di chat private, numeri di progetti interni, dati di clienti. Se fai un fork di questo repository per te, tieni quel materiale fuori dalle parti che pubblichi.

Se uno di questi dati è già stato committato, trattalo come una fuga: prima ruota la credenziale, poi pulisci la cronologia, non solo l'ultimo commit.

## Confini di fiducia

- Il Vault è un insieme di file normali. Qualunque CLI con accesso al filesystem può leggerlo e scriverlo tutto, e i permessi del sistema operativo sono l'unico controllo per file. Se più di una persona dovesse condividere un backend Cloud-Server, `docs/it/org-deployment.md` spiega cosa comporta in pratica.
- `nexgen sync` e `nexgen doctor` (i vecchi nomi `agent-sync` e `agent-doctor` funzionano ancora) girano con i permessi del tuo utente. Leggono e modificano i file di configurazione delle CLI (vedi `docs/it/what-gets-written.md`) e, nel profilo MULTI, installano un timer utente systemd su Linux o una voce in Task Scheduler su Windows. Nessuno dei due usa sudo. L'eccezione è il ruolo server: installa la policy sudo degli agenti in `/etc/sudoers.d/nexgen-agents`, una lista fissa di quindici comandi, descritta in `docs/it/roles.md`.
- I server MCP girano come processi locali o si collegano al tuo VPS. Nessuno strumento del manifest predefinito manda contenuti del Vault a un modello di terzi o a un'API SaaS durante il funzionamento normale. Ricerca semantica, OCR e scraping sono self-hosted, su infrastruttura che installi tu (`03-INFRA/deploy/`), non un servizio che questo progetto gestisce per te. Un server MCP ospitato che aggiungi tu porta con sé le proprie condizioni di privacy e sicurezza.
- Il browser MCP si attacca a una finestra Chrome vera e visibile, tramite il protocollo DevTools. Un agente che avvia un browser headless alle tue spalle è un bug.
- La modalità Cloud-Server raggiunge il tuo VPS tramite un tunnel SSH che configuri tu. Le porte e le credenziali del tunnel stanno nel tuo `99-INDEX/USER-PROFILE.md` e in `99-SECRETS/`, non in questo repository.

## Due scansioni dei segreti, due destinatari

Il motore spedisce un unico modulo di rilevamento, `03-INFRA/agent-universal-layer/leak-scan/leak_scan.py`, dietro due gate con scopi diversi:

- La scansione del Council protegge chi usa il motore ed è sempre attiva. Ogni chiamata di `council.py` controlla il brief in uscita e il testo che un seggio restituisce, prima che raggiungano o tornino da un modello di terzi. Gira per ogni utente e ogni sessione, e non si può disattivare se non rinunciando al Council. Vedi la sezione Guardrails di `docs/it/council.md`.
- La scansione in CI è il gate di pubblicazione del repository. Gira su ogni pull request e tiene fuori dalla cronologia pubblica i segreti probabili e i percorsi personali. Chi usa il motore non pubblica codice del motore, quindi questa scansione non ha niente a che vedere con il suo Vault né con le sue sessioni del Council.

Il confine che fa rispettare la regola è la protezione dei rami su GitHub: `main` richiede la CI verde e i commit firmati, e rifiuta i force push e la cancellazione del ramo. Non richiede un'approvazione, perché chi mantiene da solo non può approvare la propria pull request, e una regola che nessuno può soddisfare viene disattivata. Le comodità dello sviluppatore in locale non sono controlli di sicurezza e non fanno parte del prodotto.

## Versioni supportate

Non esiste ancora un calendario LTS o di patch. Le correzioni di sicurezza arrivano su `main`, e nessun ramo di release più vecchio riceve backport.

## Firme delle release

Ogni commit su `main` da `8fcd351` (2026-07-08) in poi porta una firma verificabile (`git verify-commit`). I tag di release si creano con `git tag -s` su una macchina di chi mantiene e si controllano con `git verify-tag`; sono accettati sia il formato OpenPGP sia quello SSH.

`nexgen release ship` firma con la chiave configurata nel git di quella macchina e, prima di fare push, verifica il tag contro l'anchor di fiducia installato lì: un tag che la flotta rifiuterebbe viene cancellato in locale e non viene mai pubblicato. Vedi [docs/it/agent-lanes.md](docs/it/agent-lanes.md#releasing).

Il workflow di release accetta solo un tag annotato, quindi un tag leggero viene rifiutato prima che qualsiasi cosa venga pubblicata. Poi verifica il tag con l'anchor di fiducia e il verificatore della release precedente, rifiuta un tag il cui commit non sta su `main` e rifiuta un commit che non ha passato i controlli richiesti da `main`. La prima release dopo la nascita dell'anchor non ha un predecessore e viene verificata contro il proprio albero; il log del workflow lo dice. Per eseguire lo stesso controllo prima di fare push:

```bash
python3 03-INFRA/scripts/nexgen_core/release_trust.py vX.Y.Z --trust-dir <trust/ of the previous release>
```

Esce con codice diverso da zero quando le copie installate rifiuterebbero il tag.

### Tag più vecchi

I tag precedenti non sono una base uniforme, e questo file in passato sosteneva il contrario. Controlla prima di fidarti:

- da `v0.1.0` a `v0.3.0` sono precedenti alla disciplina di firma e non sono firmati;
- `v0.5.0` e `v0.5.1` non sono firmati; `v0.5.2` è un tag leggero, non un oggetto tag;
- da `v0.93.0` a `v0.97.6` non sono firmati. Da `v0.93.0` il workflow di release creava il tag su un runner CI, che non ha chiavi di firma, e `--verify-tag` conferma solo che il tag esiste. Dodici release sono uscite così prima che il buco fosse trovato, il 2026-08-06;
- tutto il resto da `v0.3.1` in poi è firmato.

Un tag non firmato dopo `v0.3.0` viola la policy di release.

### Cosa verifica una copia installata

`nexgen-update` verifica l'oggetto tag della release che sta per installare, non il commit a cui punta. Per una release quel commit è un merge fatto da GitHub, la cui firma non dice niente su chi mantiene il progetto. Il controllo usa i firmatari fissati in `03-INFRA/agent-universal-layer/trust/` della copia già installata, mai quelli della release da installare: una release non può garantire per se stessa. I controlli OpenPGP girano in un keyring temporaneo che contiene solo le chiavi fissate, quindi il keyring della macchina non viene né usato come fonte di fiducia né modificato.

| Esito | Significato | Interattivo | `--unattended` |
| --- | --- | --- | --- |
| verified | firmato da una chiave fissata | installa | installa |
| bad | la firma non corrisponde al tag, o la chiave è revocata | rifiuta | rifiuta |
| untrusted | firma valida, ma di una chiave che l'installazione non fissa | rifiuta | rifiuta |
| unverifiable | tag non firmato o leggero, anchor non ancora installato, `gpg`/`ssh-keygen` assenti, chiave fissata scaduta | avvisa, decidi tu | rifiuta |

`--check` riporta lo stesso esito e rifiuta anche `bad` e `untrusted`.

Firmatari fissati oggi, le chiavi che hanno firmato le release `v2.x`:

- OpenPGP `5D06 1CE9 626C 9CC8 BD88  761F 4399 A81E 895E B96F`
- OpenPGP `6CD1 92BE 0A78 7F77 867F  3B9F 9115 22B8 F02F FA88`
- SSH `SHA256:zCkqYfvAjmrD+kCDQ9Jp0plP8fV89mM+GfavKOmGvsA`

### Cosa questo non copre

- Una copia installata prima che esistesse `trust/` non ha anchor. L'aggiornamento che porta `trust/` per la prima volta viene giudicato dall'updater già installato, che si limitava ad avvisare, quindi la garanzia parte dall'aggiornamento successivo.
- Una chiave o una macchina di chi mantiene compromessa. L'anchor decide quale firma conta, non può sapere se chi ha firmato è stato costretto o violato.
- La pubblicazione su PyPI, quando `PYPI_API_TOKEN` è impostato, usa un token API di lunga durata. Il trusted publishing con OIDC richiede prima la registrazione del publisher su pypi.org e non è attivo.

## Cambiare un firmatario

Cambiare firmatario richiede due release: spedisci la nuova chiave in una release firmata da una chiave già fissata, e solo dopo firma con quella nuova. Una release firmata da una chiave che nessuna copia installata fissa viene rifiutata su ogni macchina, con o senza intervento umano.

Anche le chiavi scadono, e quelle OpenPGP fissate scadono nel 2028. Estendile o sostituiscile in una release fatta con largo anticipo, perché una copia che non ha mai ricevuto la chiave estesa legge la firma come scaduta e la tratta come non verificabile.

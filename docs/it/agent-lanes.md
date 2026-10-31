# Sviluppo e rilasci

🇬🇧 [English](../agent-lanes.md)

`developer` è l'unico ramo di sviluppo.
Un rilascio unisce `developer` a `main` e mette il tag sull'unione verificata.
Un ramo remoto ritirato si cancella solo dopo che la sua punta è dentro `main`: i suoi commit restano nella storia del rilascio.
Non aprire un altro ramo per una correzione o per un rilascio.

## Rilascio

<a id="releasing"></a>

`nexgen release ship` esegue un rilascio intero da un checkout di sviluppo.
Lancialo su `developer` con l'albero pulito:

```sh
nexgen release ship                                  # next patch version
nexgen release ship minor --notes "One line for the changelog reader"
nexgen release ship 2.5.0 --install                  # explicit version, and update this machine
```

L'argomento del bump è `patch`, `minor`, `major` oppure una versione esplicita `X.Y.Z`.
`--notes` aggiunge una riga in cima alla sezione `## [Unreleased]` di `CHANGELOG.md`, ed è da lì che arrivano le note del rilascio.
`--install` aggiorna anche la macchina che esegue il comando.
`--repo PATH` sceglie il checkout (predefinito: il repository git della cartella corrente).
`--poll SECONDS` imposta l'attesa tra un controllo e l'altro (predefinito 30).
Il comando rifiuta di partire dal motore installato, accetta solo un checkout di sviluppo.

Stampa una riga `== step` per ogni passo e finisce con `OK vX.Y.Z` oppure `STOP <step>: <reason>`:

1. **prepare**: controlla il ramo e l'albero pulito, fa fast-forward da `origin`, alza `VERSION`, la `__version__` del pacchetto e `CHANGELOG.md`, esegue il preflight, committa `Release vX.Y.Z` e spinge `developer`.
   Se il preflight fallisce, il bump viene annullato.
   Il bump viene saltato quando `VERSION` è già avanti rispetto al tag più recente.
2. **pull request**: apre la pull request da `developer` a `main`, oppure riusa quella aperta.
   Nessuna pull request viene aperta se non cambierebbe alcun file: quando `main` porta già l'albero del ramo, che è quello che lascia `sync-developer`, il rilascio passa diretto ai controlli e al tag.
3. **smoke qui, controlli sulla pull request**: lo smoke di questa macchina (`03-INFRA/scripts/smoke.py` del checkout candidato) gira mentre GitHub esegue i controlli, e il suo esito viene pubblicato sulla testa della pull request come stato di commit, `smoke-local` su Linux e `smoke-windows` su Windows, con la durata nella descrizione.
   Anche un fallimento viene pubblicato, e non viene unito nulla.
   Poi aspetta i controlli richiesti dalla protezione del ramo `main`, letti con `gh api .../branches/main/protection/required_status_checks`.
   Un job che nessuno richiede, `engine-tests-windows` compreso, viene riportato dalla CI senza trattenere il rilascio.
   Quando la protezione non è leggibile, valgono tutti i controlli della run, come prima.
   Il controllo `lane-guard` qui viene ignorato, perché rifiuta ogni push che non arrivi da un ramo di feature.
4. **merge**: unisce con un merge commit.
5. **CI su main**: aspetta gli stessi controlli richiesti sul merge commit, oppure, quando la protezione non è leggibile, l'intera run `ci.yml`.
   Non c'è una seconda CI da aspettare quando il merge commit porta esattamente l'albero che i controlli della pull request hanno superato (`refs/pull/<n>/merge`): quei controlli sono la prova.
6. **tag**: crea il tag con `git tag -s`, firmato con la chiave che il git di questa macchina è configurato per usare.
   L'annotazione porta anche i controlli su cui il rilascio è stato verificato (`Required checks: …`), che `release.yml` rilegge, perché il suo token non può leggere la protezione del ramo `main`.
   Poi verifica il tag contro l'ancora di fiducia della copia installata (`trust/release-signers.txt`).
   Se quella copia rifiuterebbe il tag, il tag viene cancellato in locale e non viene spinto nulla.
   Un tag già presente sul remoto viene riusato, e un tag lasciato qui da una run interrotta viene verificato e spinto invece di essere creato di nuovo.
7. **release workflow**: spinge il tag, aspetta `release.yml` e lo rilancia una volta se fallisce.
   Poi controlla che la release GitHub non sia una bozza e che abbia gli asset.
8. **sync developer**: aspetta che `sync-developer` unisca `main` di nuovo dentro `developer`.
9. **install** (solo con `--install`): esegue `nexgen update --yes --target vX.Y.Z` attraverso il launcher installato (`~/.local/bin/nexgen.cmd` su Windows, `~/.local/bin/nexgen` altrove), mai attraverso il `nexgen` che risolve il PATH: lanciato da un checkout di sviluppo, quello risponde "già alla versione richiesta" parlando di sé e non aggiorna nulla.
   Una macchina senza launcher installato si ferma qui e lo dice.
   Un aggiornamento fallito e riportato indietro viene ritentato una volta; un rifiuto perché il vault ha file non committati o non tracciati non viene ritentato, e viene riportato in una riga che nomina la cartella.
   Poi controlla che la `VERSION` installata corrisponda ed esegue lo smoke del motore installato (`03-INFRA/scripts/smoke.py` dell'albero installato, con l'interprete che usano i launcher, così un albero di rilascio non ha bisogno di `.venv`).
   Una macchina che lo fallisce viene riportata alla versione da cui veniva (lo stesso launcher, `update --yes --target <previous>`) e il rilascio si ferma dicendolo.
   Infine esegue `doctor --summary` del launcher installato, che lo ferma solo sui fallimenti; gli avvisi non bloccano.

Ogni macchina del maintainer firma con la propria chiave, e quella chiave deve comparire in `trust/release-signers.txt`.
Le copie installate accettano solo i firmatari che hanno fissato, quindi una chiave nuova richiede due rilasci (vedi [SECURITY.md](../../SECURITY.it.md)).

Dopo uno stop, correggi il motivo e rilancia: riprende dal primo passo non fatto.
Una versione già alzata non viene alzata di nuovo, una pull request di rilascio aperta viene riusata, una pull request che non cambierebbe file non viene aperta, un commit di rilascio già su `main` salta dritto alla CI e al tag, e un tag già pubblicato prosegue da lì.
Un rilancio mentre `main` non è ancora tornato in `developer` chiude il rilascio già uscito (il tag viene riusato, il sync aspettato, `--install` installa la versione che trova) invece di alzare una seconda versione.

## Verifica e lavoro in parallelo

Si lavora e si committa su `developer`; i commit restano firmati e i test di regressione più vicini girano prima del commit.
Prima di spingere o di rilasciare, esegui `python 03-INFRA/scripts/check_engine.py`.
La pubblicazione pubblica richiede anche la scansione delle fughe, un'identità di firma verificata da GitHub e tutti i controlli CI richiesti.

Le sessioni in parallelo usano worktree isolati.
Un worktree staccato può tenere le modifiche di revisione finché un integrator le applica e le committa su `developer`; non deve pubblicare un ramo nuovo.
I file sporchi di un'altra sessione si preservano e la storia pubblicata non si riscrive mai.

## CI e doctor

`nexgen_core/lanes.py` possiede i controlli di ramo usati da CI e doctor.
Il job `lane-guard` controlla gli intervalli di push e le istantanee di merge proposte dalle PR:

- `developer` accetta commit di sviluppo e deve contenere il `main` pubblicato.
- `main` accetta unioni da `developer` e rifiuta commit di sviluppo diretti.
- Gli altri nomi di ramo falliscono il contratto di sviluppo.
- Una punta precedente mancante o un ref richiesto mancante fallisce in chiusura.
  Un primo push richiede una base pubblicata esplicita.

La CI riporta le violazioni dopo il push, e la protezione dei rami di GitHub decide quali controlli falliti bloccano il merge di un rilascio.
Doctor accetta il lavoro locale su `developer`, avvisa per il lavoro su `main` o su rami ritirati, e riporta i ref remoti mancanti come non verificati.
Non sposta e non committa mai quel lavoro.

## Allineamento automatico

`.github/workflows/sync-developer.yml` parte dopo che `main` avanza e si può anche avviare a mano.
Il suo entry point Python chiede a GitHub un merge atomico di `main` dentro `developer`; un ramo già allineato è un no-op riuscito.
Conflitti, rami mancanti e errori di trasporto fermano il workflow.
Un conflitto si risolve su `developer`, si verifica il risultato e si spinge quel ramo.
Il workflow non crea rami temporanei e non fa mai force-push.

La cancellazione automatica dei rami uniti deve restare disattivata nelle impostazioni del repository, così un rilascio lascia `developer` disponibile.
I checkout locali ricevono lo stato remoto risultante con un fetch normale e un pull in fast-forward; il workflow non tocca mai i worktree locali.

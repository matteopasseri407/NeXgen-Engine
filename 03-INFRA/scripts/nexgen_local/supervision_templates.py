"""The two files a new task starts from: the brief the supervisor fills and the report.

One job: hold the text of `20-brief-MODELLO.md` and `30-resoconto-MODELLO.md`, the
two models `sup start` writes into a new task folder. They are prose, not rules:
what the engine enforces lives in the gates, and these are what a person is asked
to fill. The brief asks for the proof tabs to be closed, because a left-open tab
is invisible to every gate and still holds memory.
"""
from __future__ import annotations


def brief_template() -> str:
    """The brief fields the supervisor must fill; every one is required."""
    return """# Brief dell'unità <unità> — ruolo <ruolo>

Chi inizia da zero, in una sessione nuova, non deve leggere altro.
Compila ogni campo, cancella queste istruzioni.

## Identificativo e ruolo
<unità> · <ruolo>

## Obiettivo
Una riga sola.

## Contesto minimo
File e percorsi esatti, comandi, vincoli.

- ...

## Risultato atteso
Il file o il commit che deve esistere alla fine.

## Confine
- File consentiti: ...
- File vietati: ...

## Verifica
Durante il lavoro lancia solo i test dei file toccati e dei moduli vicini, mai la suite intera (che riesegue `sup collect` in modo indipendente):
```
pytest <file dei test toccati>
python 03-INFRA/scripts/discipline.py check --base <base>
python 03-INFRA/scripts/ruff_baseline_check.py
python 03-INFRA/agent-universal-layer/leak-scan/leak_scan.py --patterns 03-INFRA/agent-universal-layer/leak-scan/leak_patterns.yaml --repo . --commit-range <base>..HEAD
```

## Fatto quando
La condizione osservabile che chiude l'unità.

## Cosa non fare
Niente push, niente tag, niente test esistenti modificati o cancellati, niente bump di VERSION, CHANGELOG.md o .github.

## Niente resta aperto
Ogni scheda, sessione o processo che apri per una prova va chiuso a fine prova:
`sup pulizia` elenca quello che il motore ha lasciato aperto, e il resoconto dice
cosa hai aperto e come l'hai chiuso.

## Formato del resoconto atteso
I campi di `30-resoconto-MODELLO.md`, solo fatti con la prova incollata.
"""


def report_template() -> str:
    """The report fields the executor must fill; every one is required."""
    return """# Resoconto dell'unità <unità> — ruolo <ruolo>

Solo fatti.
Una dichiarazione senza la prova incollata non vale.

## Stato
fatto | bloccato

## Commit sul ramo locale
- <hash> <titolo>

## File toccati
| file | righe aggiunte | righe rimosse |
|---|---|---|

## Comandi eseguiti
```
<comando>
<ultime righe del risultato, incollate>
```

## Test nuovi
Quali sono e, quando l'unità è un fix, la prova che falliscono sul codice precedente.

## Processi e schede aperti
Cosa hai aperto per le prove e come l'hai chiuso, con il comando esatto.
Se qualcosa è rimasto aperto, scrivilo qui invece di tacerlo.

## Dubbi e richieste al proprietario
Cosa chiedi, senza averlo eseguito in proprio.

## Prossimo passo se bloccato
Cosa serve per ripartire.
"""

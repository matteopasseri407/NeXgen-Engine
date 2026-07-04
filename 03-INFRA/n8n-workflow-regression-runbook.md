---
tags:
  - infra
  - n8n
  - runbook
status: active
type: runbook
---

# n8n Workflow, runbook anti-regressione

Procedura obbligatoria per modificare, pubblicare o diagnosticare qualsiasi workflow n8n. Le regole valgono per tutti i workflow: trigger, trasformazione, LLM/API, parser, persistenza, output esterno.

## Regola fondamentale

Un workflow `success` non prova che lo screener abbia funzionato.
La verifica è valida solo se l'esecuzione di produzione completa la sua catena reale end-to-end.

Il comando MCP `execute_workflow` può indicare che l'avvio è stato accettato mentre l'esecuzione è ancora `running`, sospesa o successivamente cancellata.
Controllare sempre `execution_entity.status`, `stoppedAt` e i dati dei nodi.

## Pubblicazione n8n 2.x

Le esecuzioni schedulate usano lo snapshot in `workflow_history` puntato da `activeVersionId`.
Modificare soltanto `workflow_entity.nodes` non aggiorna la produzione.

Procedura:

1. Esportare e salvare un backup del workflow.
2. Creare un nuovo `versionId` e una nuova riga in `workflow_history`.
3. Aggiornare il draft in `workflow_entity`.
4. Pubblicare esplicitamente la nuova versione.
5. Verificare che `versionId = activeVersionId`.
6. Verificare nuovamente cron, timezone e `active=true`.

## Contratto prompt e parser LLM

Per ogni nodo LLM con testo dinamico:

- `parameters.text` deve iniziare con `=`.
- La coda del prompt deve contenere l'espressione dinamica dei dati, non `{{ ... }}` come testo letterale.
- Se il modello è configurato per `json_object`, il parser deve accettare JSON puro `{ ... }`.
- Prompt e parser devono usare lo stesso formato. Non combinare JSON puro con parser basato solo su delimitatori legacy.
- Il prompt deve imporre: primo carattere `{`, ultimo `}`, nessun ragionamento, Markdown o testo esterno.

Segnale di regressione:

- il nodo batch contiene dati reali;
- il nodo LLM restituisce `analyzed: 0`;
- oppure restituisce ragionamento libero e nessun JSON.

## Giudici LLM con reasoning

I modelli "reasoning" normalmente mettono il pensiero in `reasoning_content` e il JSON in `content`. Ma sotto pressione di budget token il ragionamento **rifuoriesce dentro `content` e tronca il JSON**. Due difese obbligatorie, sempre insieme:

1. **Headroom di token.** `maxTokens` ampio. Il costo è trascurabile, la troncatura del JSON sparisce.
2. **Parser tollerante.** Mai `JSON.parse` diretto sull'output. Estrarre il JSON con brace-matching (ignorando testo/ragionamento prima o dopo) e, se troncato, recuperare i singoli oggetti completi via regex. Marcare "batch fallito" SOLO se non si recupera nessun oggetto. Così un hiccup del modello non perde dati né genera falsi alert.

Inoltre rendere non-fatali i nodi esterni critici (LLM, scritture DB, notifiche) con `onError: continueRegularOutput` + `retryOnFail`, così un outage diventa un esito leggibile e non un crash che fa scattare l'error-workflow.

## Controlli per workflow con batching

- Scegliere una batch size che eviti sia il troncamento dell'output LLM sia il superamento della finestra MCP (5 minuti tipici). Testare con volumi reali.
- Deduplica intra-run prima del batching per le chiavi rilevanti.

## Deduplica e scritture

Non scrivere nel database prima che il parser abbia prodotto un verdetto valido.
Un batch LLM fallito non deve trasformare automaticamente tutti gli item in `PASS`.

Prima del run di verifica:

- fare backup delle righe che si intende rimuovere;
- eliminare soltanto le chiavi contaminate dal run difettoso;
- non svuotare le cache globali alla cieca.

Deduplicare dentro la stessa esecuzione oltre che contro il database.

## Output esterni

Se il contratto del workflow prevede una notifica o un messaggio, ogni run deve produrre un esito osservabile anche quando non trova elementi utili.
Il riepilogo deve leggere le statistiche direttamente dai nodi che le producono, non dai metadati `pairedItem` dopo batching LLM.

Il messaggio deve contenere numeri reali: item ricevuti, analizzati, scartati, duplicati, passati al gate, valutati dal LLM, esiti per categoria, errori API e batch falliti.

La consegna è verificata solo dalla risposta positiva del sistema esterno.

## Come distinguere un esito vuoto reale da una pipeline rotta

Zero reale:

- le fonti hanno prodotto dati oppure un conteggio esplicito pari a zero;
- il gate ha statistiche numeriche;
- la somma `analyzed` dei batch LLM coincide con gli item inviati al LLM;
- ogni batch contiene JSON valido;
- `has_partial_failures=false`;
- il parser produce conteggi numerici;
- la notifica risponde `ok=true`.

Pipeline rotta:

- `N/A` nel riepilogo;
- item nei batch ma `analyzed: 0`;
- item inviati al LLM senza output JSON;
- `success` con nodo notifica non eseguito;
- esecuzione ancora `running` o cancellata;
- falsi `PASS` creati come fallback di batch non parsabili;
- `versionId` diverso da `activeVersionId`.

## Gate di accettazione prima di lasciare lo schedule attivo

Non dichiarare conclusa una modifica finché non sono veri tutti i punti:

1. Backup presente.
2. Nuova versione pubblicata.
3. `activeVersionId` corretto.
4. Cron e timezone verificati.
5. Run di produzione con `status=success` e `stoppedAt` valorizzato.
6. Tutti i batch LLM parsabili.
7. Conteggio item in ingresso uguale agli item valutati, salvo dedup/scarti esplicitamente numerati.
8. Nessun errore parziale.
9. Scritture database coerenti.
10. Notifica `ok=true`, `message_id` presente e nessun `N/A`.

## Regola: LLM negli healthcheck

Negli healthcheck operativi l'LLM può tradurre l'alert, ma non deve decidere lo stato.
Il gate deve restare deterministico e produrre fatti minimi: workflow, stato attivo, HTTP/status, execution o conteggi verificabili quando disponibili.
Il nodo LLM deve stare a valle del gate e deve avere fallback tecnico: se modello, gateway o parser falliscono, l'alert viene comunque inviato con il testo deterministico.

## Verifica live senza MCP n8n

`n8n execute` da CLI nel container in produzione può fallire: collide sul Task Broker e, in modalità `internal`, non inizializza il license provider → la decifratura delle credenziali fallisce. Quindi la CLI non verifica i nodi che usano credenziali. Per la verifica end-to-end usare l'istanza live: pubblicare una versione temporanea con cron a +pochi minuti, riavviare, far partire il run reale, poi ripristinare `activeVersionId` alla versione definitiva e cancellare la temporanea.

## Note correlate

- `03-INFRA/remote-automation.md`

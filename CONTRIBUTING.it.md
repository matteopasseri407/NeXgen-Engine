# Contribuire

🇬🇧 [English](CONTRIBUTING.md)

Questo è un progetto con un solo maintainer. I contributi sono benvenuti, quindi regola le aspettative: la revisione
può richiedere tempo, e non ogni pull request rientra nello scopo del progetto anche quando è fatta bene (vedi "Design
boundaries" e "Scope and limitations" in [README.md](README.it.md)).

## Prima di scrivere codice

Per un contributo esterno più grande di una piccola correzione, apri una issue che descrive il problema e l'approccio
che proponi. Ti risparmia dal costruire qualcosa che non segue la direzione del progetto. Per una correzione piccola e
ovvia, come un refuso, un link rotto o un bug chiaro con una correzione di una riga, basta la pull request.

Per il lavoro di manutenzione assistito dall'AI, parti da [AGENTS.md](AGENTS.md), poi dalla tabella dei proprietari qui
sotto. Un compito di manutenzione autorizzato usa `developer` e un worktree isolato; non richiede una issue nuova né un
ramo per ogni correzione. Leggi i chiamanti e i test coinvolti prima di modificare. Cambia il proprietario, mantieni la
compatibilità al confine esistente, e integra dopo la verifica.

## Preparare l'ambiente di sviluppo

Usa Python 3.11 o più recente e installa le dipendenze di sviluppo:

```sh
python -m venv .venv
# Attiva .venv con il comando di attivazione della tua shell.
python -m pip install -e '.[dev]'
python 03-INFRA/scripts/check_engine.py
```

L'ultimo comando esegue il cancello della baseline Ruff committata, il cancello di disciplina su dimensioni e shell (un
file o una funzione troppo grande può esistere ma non crescere) e l'intera suite pytest. Si ferma al primo cancello che
fallisce e non invoca mai sync o release. Per un cambiamento circoscritto, passa prima il file di test pertinente ed
esegui il comando completo prima dell'integrazione. Correggi i nuovi rilievi del linter invece di rigenerare la
baseline.

### Dove la suite mette i file temporanei

Su Windows `%TEMP%` è `C:\Users\<tu>\AppData\Local\Temp`, dentro la tua home, e il gateway di OpenClaw osserva la home
con un solo handle ricorsivo: le migliaia di file che una suite crea e cancella lì sono una tempesta di eventi che non
riesce a piegare. `tests/conftest.py` imposta quindi `PYTEST_DEBUG_TEMPROOT` prima che esista il primo `tmp_path`, e
pytest tiene i suoi alberi numerati in `%SystemDrive%\Temp\nexgen-<utente>`, oppure in `%PUBLIC%\nexgen-<utente>` quando
il primo non è scrivibile. Imposta `NEXGEN_SCRATCH_ROOT` per scegliere tu la cartella; un `PYTEST_DEBUG_TEMPROOT` già
impostato da chi chiama non viene mai sovrascritto, ed è ciò su cui conta un job CI puntato a un disco veloce. pytest
ruota gli ultimi tre alberi numerati e cancella gli altri. Su Linux la directory temporanea di sistema è già fuori dalla
home e non cambia niente.

### Trovare il modulo giusto

| Comportamento | Proprietario | Test più vicini in `03-INFRA/agent-universal-layer/tests/` |
| --- | --- | --- |
| Scritture atomiche, backup, artefatti privati | `nexgen_core/files.py` | `test_nexgen_foundations.py`, `test_nexgen_quality_regressions.py` |
| Aggiornamento e rollback dei pin di terze parti | `nexgen_core/thirdparty_bump.py` | `test_nexgen_bump.py`, `test_nexgen_bump_file_contract.py` |
| Ordine delle fasi di sync e stato dei guasti | `nexgen_core/guard.py` | `test_nexgen_phase3.py`, `test_nexgen_quality_regressions.py` |
| Download, sostituzione e pin delle skill | `nexgen_core/skill_sources.py` | `test_nexgen_skill_fetch.py`, `test_nexgen_skill_versions.py`, `test_nexgen_lazy_skills.py`, `test_nexgen_quality_regressions.py` |
| Pagina di catalogo delle skill (`~/.agents/skills/INDEX.md`) | `nexgen_core/skill_index.py` | `test_nexgen_skill_deps.py`, `test_nexgen_openclaw_sync_skills.py` |
| Lavori automatici di OpenClaw e il suo osservatore di configurazione | `nexgen_core/openclaw/background.py`, `nexgen_core/openclaw/observation.py` | `test_nexgen_openclaw_background.py`, `test_nexgen_openclaw_observation.py` |
| Dove un processo mette i suoi file temporanei | `nexgen_core/scratch.py`; i chiamanti sono `tests/conftest.py` e `nexgen_local/supervision_pytest.py` | `test_nexgen_pytest_scratch_root.py` |
| Lock dell'host | `nexgen_core/lock.py` | `test_nexgen_lock.py` |
| Ripiego della versione in un checkout sorgente | `nexgen_local/version.py` | `test_nexgen_command_surface.py` |
| Dispatch della CLI locale e adapter condivisi di configurazione e LLM | `nexgen_local/cli.py`, `nexgen_local/cmds/base.py`; comandi di dominio in `cmds/` | `test_nexgen_command_surface.py`, `test_nexgen_local_help_surface.py`, `test_nexgen_cmdsservicesplit_pinning.py` |
| Persistenza dei token OAuth e payload di refresh | `nexgen_local/connectors/token_store.py`; richieste ai provider in `auth.py` e `outlook.py` | `test_nexgen_stabilization_contracts.py`, `test_nexgen_smallfamsplit_pinning.py` |
| Cache degli aggiornamenti, avvisi sulle skill, richieste e hook di shell e avvio | `nexgen_core/tools/notifier_state.py`, `notifier_skills.py`, `notifier_prompt.py`, `notifier_boot.py` | `test_nexgen_update_notifier.py`, `test_nexgen_stabilization_contracts.py` |
| Percorsi sorgente, sanificazione del testo, termini di ricerca | `nexgen_local/source_selection.py` | `test_nexgen_local.py` |
| Esiti dei tool e ricevute di audit | `nexgen_local/tools.py` | `test_nexgen_tool_outcomes.py`, `test_nexgen_toolssplit_pinning.py` |
| Affermazioni nelle risposte e ricevute di lettura riuscita | `nexgen_local/evidence.py` | `test_nexgen_local.py`, `test_nexgen_quality_regressions.py` |
| Instradamento e pipeline di risposta, controllo delle affermazioni | `nexgen_local/engine.py` | `test_nexgen_local.py`, `test_nexgen_local_tiers.py` |
| Richieste ai modelli e scadenze | `nexgen_local/llm.py` | `test_nexgen_llm_deadlines.py` |
| Id delle proposte, approvazione esclusiva e tentativi durevoli | `nexgen_local/proposals.py`; il cancello di upload in `drive_mcp.py` | `test_nexgen_proposal_execution.py`, `test_nexgen_local_drive_mcp.py`, `test_nexgen_gatefamssplit_pinning.py` |
| Ciclo di vita dei processi del Council e checkpoint del relay | `03-INFRA/agent-universal-layer/council/` | `test_nexgen_council_*.py` |
| Componenti di terze parti dentro i moduli (`upstream:` nel catalogo dei moduli) | `nexgen_core/modules.py` (la dichiarazione), `nexgen_core/depwatch.py` (legge il pin, interroga monte) | `test_nexgen_module_upstream.py`, `test_nexgen_depwatch_abandoned.py`, `test_nexgen_playwright_pin.py` |
| Flag delle sedute del Council contro le CLI dei fornitori installate (`nexgen council contract`) | `council/cli_contract.py`; i flag vengono da `_build_seat_command` in `council/seat_process.py` | `test_nexgen_council_cli_contract.py` |
| Pulizia dei sottoprocessi posseduti e adapter di avvio Windows | `nexgen_core/processes.py` | `test_nexgen_council_process_integration.py`, `test_nexgen_mcp_transport.py`, `test_vault_groom.py` |
| Pubblicazione del Vault e file selezionati | `nexgen_core/git_ops.py` | `test_nexgen_scoped_publish.py` |
| Indicizzazione del Vault MCP autonomo e pubblicazione delle note | `deploy/vault-mcp/src/vault_mcp_server/vault.py` | `test_deploy_vault_mcp.py` |
| Cancelli dei percorsi OCR e letture di immagini con limite | `deploy/ocr/mcp/vault_ocr_mcp.py` | `test_deploy_ocr_mcp.py` |
| Politica di montaggio MCP e conservazione dei connettori privati | `nexgen_core/renderer.py`; scrittori di dialetto in `mcp_render/` | `test_nexgen_mcp_preservation.py`, `test_nexgen_phase2.py`, `test_nexgen_lazy_mcp.py` |
| Scadenze MCP pigre, incorniciatura e correlazione delle risposte, concorrenza, ambiente dei figli, separazione lettura/scrittura | `03-INFRA/agent-universal-layer/mcp/lazy-mcp.py` (vedi [lazy-mcp.md](docs/it/lazy-mcp.md)) | `test_nexgen_mcp_transport.py`, `test_nexgen_lazy_mcp.py`, `test_nexgen_lazy_mcp_serving.py` |
| Aggiornamento a release del motore e pin meccanico | `nexgen_core/updater.py` | `test_nexgen_update_command.py` |
| Server MCP e skill installati: provenienza, pin, stato a monte (`nexgen info`) | `nexgen_core/extensions.py`; le sezioni in `nexgen_core/tools/info.py` (vedi [whats-installed.md](docs/it/whats-installed.md)) | `test_nexgen_extensions.py` |
| Guardrail di OpenCode: cartella di plugin, hook V2, pulizia storica | `agent-universal-layer/hooks/opencode-guardrail-plugin.mjs`, `nexgen-guardrail-core.mjs`; `nexgen_core/runtimes/opencode.py` | `test_nexgen_guardrail_adapters.py`, `test_runtimes.py`, `test_nexgen_opencode_live.py` (binario reale, saltato se manca) |
| Skill distribuite dal motore, tenute come copie congelate nel Vault (`skills adopt`, `skills.engine_copies`) | `nexgen_core/skill_adopt.py`; `check_skill_engine_copies` in `nexgen_core/checks/skill_checks.py` | `test_nexgen_skill_adopt.py` |
| Da dove viene la credenziale di un server (processo, `environment.d`, deposito dei segreti) | `_declared_value` in `agent-universal-layer/mcp/lazy-mcp.py`; `nexgen_core/deposit_env.py` | `test_nexgen_mcp_credentials.py`, `test_nexgen_mcp_trim.py` |
| Alzare i pin di terze parti, provando ogni server MCP prima di tenere il cambiamento | `nexgen_core/thirdparty_bump.py`; `verify_servers` in `nexgen_core/mcp_check.py` | `test_nexgen_bump.py`, `test_nexgen_mcp_check.py` |
| Corsie dei contributori | `nexgen_core/lanes.py` | `test_nexgen_lanes.py` |
| Allarme sulle dimensioni (coda di split), cancello shell e PowerShell, integrità dei test, prove di test per commit; brief di revisione facoltativo e decisione del proprietario | `nexgen_core/discipline/` (`python 03-INFRA/scripts/discipline.py`) | `test_nexgen_discipline_*.py` |

Controlla i nomi reali dei file prima di scegliere un test. I moduli a grafo guidano le funzioni decisionali esistenti;
non tengono una seconda implementazione. Importa gli helper dal loro proprietario. `engine.py` conserva export di
compatibilità per i chiamanti più vecchi, ma i consumatori nuovi usano il modulo proprietario.

Il recupero interno usa `ToolRegistry.call_result`: `status` dichiara `ok`, `empty` o `error`, `usable` decide se
consumare il risultato e `text` contiene il contenuto. Non dedurre mai un esito da parentesi o dalla formulazione di un
messaggio. Le implementazioni dei tool, doppi di test compresi, registrano esplicitamente risultati vuoti ed errori.
L'audit deve riuscire prima che una ricevuta entri nel registro. Serializza le ricevute con `ToolCall.receipt()` così i
checkpoint di ricerca conservano lo stato quando riprendono. I metodi solo testuali restano disponibili per i chiamanti
CLI e MCP; l'interpretazione del testo di rifiuto è limitata ai checkpoint più vecchi senza stato.

Gli aggiornamenti dei pin usano `nexgen_core/files.py` invece di un secondo scrittore atomico. Gli artefatti del Council
e gli snapshot di recupero usano lo stesso proprietario; `backup_file` accetta uno snapshot testuale già letto senza
generare una seconda politica di nomi. I dialetti MCP condividono `McpRenderer.unmounted_server_names` per le decisioni
di montaggio. Download e collocazione delle skill condividono `github_skill_source` per i confini dei repository. I
registri di versione delle skill devono essere mappe leggibili da nomi a stringhe di versione. Un registro non valido
viene conservato e segnalato dal doctor. Una scrittura di pin fallita fa fallire il sync; i byte cambiati da soli non
certificano un aggiornamento completato. Il lock dell'host del registro serializza la sua lettura e la sua scrittura,
conservando i pin degli scrittori concorrenti senza annidare il lock globale del sync.

Council, MCP pigro e grooming del Vault condividono `nexgen_core/processes.py` per terminare alberi di sottoprocessi
posseduti. Passa solo un gruppo di processi creato da chi chiama. L'MCP pigro applica le scadenze di avvio e dei tool del
manifest all'I/O RPC, comprese le scritture sulla pipe, e limita i byte ricevuti a 8 MiB per scambio. Il provisioning ha
scadenze proprie. Un processo di supporto che il gateway può raggiungere dichiara il proprio stdin (`DEVNULL`), perché
lo stdin del gateway è il flusso del protocollo, e `test_nexgen_subprocess_stdin_contract.py` fa fallire un avvio che lo
omette. Una chiamata a un tool andata in timeout ha esito sconosciuto e non viene mai ritentata automaticamente. Solo
risposte JSON-RPC corrispondenti completano una richiesta; notifiche e risposte ad altre richieste no.

Il Council cattura al massimo 8 MiB tra stdout e stderr per invocazione e legge al massimo 8 MiB dal file di risultato
autorevole. Un overflow sveglia il watchdog anche quando stdout è silenzioso. `output_limit` e `invalid_output` sono
guasti terminali e non devono innescare tentativi automatici né una seduta di ripiego. I comandi dell'updater del motore
usano lo stesso proprietario dei processi, con un budget Git di 120 secondi e un budget di comando di 600 secondi. I
guasti dopo l'inizio della mutazione conservano il commit precedente e le istruzioni di recupero manuale. Non dedurre un
rollback da un comando fallito; verifica il ref reale prima di descrivere l'esito.

I tipi dei campi JSON del router vengono validati prima che percorsi o parole chiave siano usati; le forme malformate
usano un instradamento deterministico senza un'altra richiesta al modello. Il cancello di upload tiene
`proposals.proposal_lock` prima di caricare l'artefatto fino alla memoria finale. Un rifiuto di prendere il lock libera
qualsiasi acquisizione precedente senza consumare la proposta. Dopo la validazione e la ricevuta di intento,
`record_attempt` persiste `attempted_at` prima di chiamare il provider. Un tentativo precedente senza completamento
salvato è incerto e non può essere ripetuto, nemmeno dopo un'eccezione o un'interruzione. Controlla il suo esito reale
prima di preparare una nuova proposta, e non azzerare mai il campo del tentativo per riprovare. `applied_at` resta prova
di un completamento restituito e salvato; un tentativo da solo non va descritto come successo né come pronto per
l'approvazione. Gli artefatti più vecchi omettono `attempted_at` e si caricano con il suo valore vuoto. Gli id delle
proposte appartengono a `proposals.py`, che `drive_mcp.py` importa per id, hash e registri dei tentativi. Creazione e
aggiornamento delle proposte Drive usano lo scrittore atomico privato. Il vecchio marcatore `sending_at` viene letto come
`attempted_at`, anche negli elenchi. I metadati di upload vengono legati allo staging con un'impronta versionata. Le
impronte mancanti richiedono una proposta nuova; quelle vecchie e non ambigue restano leggibili. Le impronte rilevano
campi cambiati; non sono firme contro uno scrittore che controlla l'intero JSON. L'id dell'artefatto deve corrispondere
al nome del file bloccato prima di caricare il cancello, e cambiare un id non deve mai dirottare il tentativo durevole
in un file diverso.

Esecuzione e pulizia del relay del Council condividono `session.session_run_lock`, tenuto fino alla rimozione. Il suo
file di lock vive fuori dall'albero delle sessioni cancellabile e non viene mai rimosso. Vengono rispettati anche i lock
più vecchi dentro l'albero. Windows può conservare una sessione più vecchia quando il suo lock storico aperto impedisce
la rimozione.

`cmds/base.py` possiede la configurazione della CLI locale e l'adapter dei modelli, compreso il confine di compatibilità
per i chiamanti più vecchi che fanno patch su `cli._config` o `cli._llm`. I comandi di dominio importano quegli helper
direttamente. `update_notifier.py` tiene i punti di ingresso dei comandi; cache, avvisi sulle skill, richieste e hook di
avvio hanno ciascuno il proprio proprietario. I modelli degli hook di shell vivono solo in `notifier_boot.py`. Google e
Outlook condividono pubblicazione dei token e fusione dei payload di refresh tramite `token_store.py`, mentre ogni
provider mantiene il proprio endpoint e i propri scope. La diagnostica dei guasti su credenziali e manifest registra
l'operazione e il tipo di eccezione; i messaggi grezzi delle eccezioni possono contenere URL di token o contenuti di
configurazione, quindi tieni quei payload fuori dai log.

Per un bug, aggiungi prima un test che fallisce per il comportamento segnalato. Controlla anche il percorso di guasto
oltre a quello di successo: una scrittura fallita deve conservare i byte vecchi, una sostituzione fallita deve
ripristinare la fonte attiva, e un errore operativo del modello non deve causare un tentativo non richiesto. Riesegui i
test pertinenti dopo la correzione, ispeziona il diff, poi esegui il cancello completo prima del merge. Registra ogni
piattaforma o provider live non testato; un endpoint sintetico dimostra il comportamento del driver, non la disponibilità
in produzione di un provider.

### Lavorare accanto a un motore installato

Segui il contratto developer e main in [AGENTS.md](AGENTS.md). Se un'altra sessione usa lo stesso checkout, crea un
worktree Git separato; cambiare ramo in una cartella condivisa non isola i file delle due sessioni.

Preferisci le fixture sintetiche della suite di regressione per i cambiamenti di runtime. I test reindirizzano i percorsi
coinvolti e disattivano le mutazioni dell'host. La fixture condivisa rimuove le sostituzioni di configurazione e stato
ereditate, e i test di override forniscono percorsi temporanei espliciti. Un sync manuale è un'operazione di scrittura
dal vivo se non lo isoli apposta: `NEXGEN_HOME` cambia la home predefinita, ma `CODEX_HOME`, `XDG_CONFIG_HOME`,
`AGENT_STATE_DIR` e `AGENT_VAULT_DATA` possono comunque selezionare percorsi espliciti. Usa percorsi usa e getta per
tutti e imposta `NEXGEN_DISABLE_HOST_MUTATIONS=1` prima di un esperimento manuale. Non puntare mai un sync di sviluppo a
una configurazione installata per vedere cosa succede.

### Prima di taggare una release

```sh
python3 03-INFRA/scripts/nexgen_core/release.py preflight
```

Controlla le cose che sono state dimenticate almeno una volta: che `VERSION` sia una versione vera e più nuova dell'ultimo
tag, che i launcher a cui puntano i symlink della release precedente corrispondano alla tabella che li genera, che
nessuno strumento privato del maintainer sia arrivato nell'albero pubblico, e che il cancello del linter passi invece di
essere stato rigenerato.

`nexgen release ship` esegue questo preflight da solo, quindi un maintainer lo usa a parte solo per controllare una
release prima di iniziarla.

## Cosa controlla la CI su ogni PR

Tutto questo gira da solo (`.github/workflows/ci.yml`) e deve passare:

- Controlli di sintassi Python, YAML e shell, più `install.sh --check`
- Una scansione dei segreti su ogni commit aggiunto dalla PR (segreti, percorsi personali scritti a mano; vedi
  [SECURITY.it.md](SECURITY.it.md))
- `ruff` contro una baseline committata (`03-INFRA/ruff-baseline.json`), che fallisce solo su un rilievo nuovo o su uno
  esistente peggiorato, non sul debito preesistente
- `shellcheck` su ogni file `.sh`
- `pip-audit` su ogni `requirements*.txt`
- Validazione `docker compose config` per ogni stack di deploy
- Analisi statica `PSScriptAnalyzer` su ogni file `.ps1`
- Uno smoke test dal vivo del server `vault-mcp` incluso: build, esecuzione, percorso di scrittura MCP reale, verifica
  del commit
- L'intera suite di regressione pytest, su `ubuntu-latest` e su `windows-latest`

Una pull request con la CI rossa non viene unita. Se un controllo fallisce e ritieni che sbagli, per esempio un falso
positivo nella scansione dei segreti, dillo nella pull request invece di aggirare il cancello.

## Guardrail di scopo da conoscere prima di iniziare

- **Note e infrastruttura hanno percorsi di scrittura separati.** Se il tuo cambiamento tocca come si scrive nel Vault,
  conserva il confine di scrittura documentato in [README.md](README.it.md) e prova il percorso di commit MCP reale.
- **Il multipiattaforma fa parte del "fatto".** Un cambiamento di runtime richiede i cancelli di test Linux e Windows,
  oppure un limite documentato esplicitamente. Metti il comportamento condiviso in Python e tieni sottili i launcher di
  shell. Vedi la regola "Definition of done cross-platform" in `03-INFRA/agent-universal-layer/instructions/AGENTS.md`.
- **Non modificare a mano i file generati.** `render.py`, `agent_sync.py` e `skills-sync.py` generano la configurazione
  per CLI dai manifest canonici. Se un dialetto generato è sbagliato, correggi il generatore, non l'output.

## Pubblicare

Committa lo sviluppo su `developer`, seguendo [AGENTS.md](AGENTS.md), con le note di rilascio sotto `## [Unreleased]` in
`CHANGELOG.md`. Un maintainer pubblica dal checkout di sviluppo con un solo comando:

```sh
nexgen release ship minor --install   # oppure patch / major / X.Y.Z
```

Apre la pull request da `developer` a `main`, la unisce solo dopo che i controlli richiesti passano, firma il tag di
release sul merge verificato con la chiave di quella macchina (rifiutandolo se le copie installate non accetterebbero il
firmatario), e aspetta il workflow di release e il merge di `main` di nuovo in `developer`. Entrambi i rami restano. I
passi, e come riprende un'esecuzione fermata, sono in [docs/agent-lanes.md](docs/it/agent-lanes.md#rilascio).

## Licenza

Inviando una pull request accetti che il tuo contributo possa essere incluso in questo progetto con la sua licenza
esistente ([PolyForm Noncommercial 1.0.0](LICENSE)).

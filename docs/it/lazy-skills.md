# Skill pigre

🇬🇧 [English](../lazy-skills.md)

Le skill sono manuali di compito facoltativi. NeXgen tiene piccolo il livello sempre attivo: `AGENTS.md` porta la
politica e la consapevolezza degli strumenti, il manifest MCP porta i tool, e il corpo di una skill si apre solo quando
il compito la richiede.

## Struttura

| Percorso | Scopo |
|---|---|
| `03-INFRA/agent-universal-layer/skills/skills.manifest.yaml` | Scelta canonica delle skill, valida per tutte le macchine. |
| `~/.agents/skill-library/` | Corpi completi gestiti. Non è una radice di scoperta. |
| `~/.agents/skills/INDEX.md` | Catalogo generato, molto piccolo. Accanto non stanno corpi `SKILL.md` facoltativi. |
| `~/.claude/skills/` | Viste native di Claude, quando Claude è un target. |
| `~/.codex/skills/` (o `$CODEX_HOME/skills`) | Viste native per skill, quando Codex è un target. È l'unica radice di Codex: non legge `~/.agents/skills`. |
| `~/.gemini/antigravity-cli/skills/` | Viste native di Antigravity, quando `antigravity` è un target. Nella TUI di agy compaiono come comandi `/name`. |

Ogni voce del manifest ha un `origin`, dei `targets` facoltativi e un'`exposure`.

`targets` decide dove viene scritta una vista, ed è l'unica cosa che lo decide. Una skill dichiarata ottiene una vista
in ogni runtime che nomina. Claude, Codex e Antigravity leggono una cartella propria, quindi una skill che non li
nomina non è scopribile lì. OpenCode e OpenClaw leggono anche il catalogo condiviso `~/.agents/skills`, oltre alle
proprie cartelle.

`exposure` è registrata nel manifest e mostrata in `INDEX.md`. Non governa le viste: una skill dichiarata le ottiene
qualunque sia la sua exposure, perché eliminarle aveva reso le skill irraggiungibili in silenzio dopo ogni sync. Il
caricamento passa da `INDEX.md` e dalla lettura su richiesta.

- `lazy` è il valore predefinito.
- `eager` e `core` marcano i comandi iniziali che il motore mette in mostra in `~/.agents/skills`.
- `manual` marca le voci tenute fuori da quella vetrina, come gli alias deprecati.

Ci sono altri due campi facoltativi per un piccolo team che condivide questo impianto:

- `scope: personal | team`, predefinito `team`. Qualsiasi valore diverso da `personal` arriva su ogni macchina.
- `owner: <member-id>`, l'identificativo di chi possiede una skill personale.

`scope: personal` filtra solo quando `AGENT_TEAM_MEMBER` è impostata. Una skill personale viene materializzata sulla
macchina in cui `AGENT_TEAM_MEMBER` corrisponde al suo `owner`, saltata altrove con una riga che lo dice, e
materializzata ovunque quando la variabile non è impostata.

Ogni skill gestita si può cercare e leggere su richiesta:

```bash
nexgen skill list                    # elenca tutte le skill gestite
nexgen skill find debugging          # richiede almeno un termine
nexgen skill show systematic-debugging
```

(I nomi storici `agent-skill list|find|show` funzionano ancora come alias.)

## Skill che diventano comandi su ogni CLI

Ogni runtime per cui NeXgen scrive usa la stessa forma agentskills.io (`<name>/SKILL.md`, frontmatter con `name` e
`description`) e permette di invocare esplicitamente una skill scoperta. Dichiari una skill una volta e la stessa
procedura è invocabile ovunque:

```yaml
my-command:
  origin: vault
  targets: [claude, codex, antigravity, opencode, openclaw]
  exposure: core
```

- `claude` collega la vista per Claude Code (`/my-command`).
- `codex` la collega in `$CODEX_HOME/skills` (predefinita `~/.codex/skills`), l'unica radice di Codex, dove compare
  come menzione `$my-command`.
- `antigravity` la collega in `~/.gemini/antigravity-cli/skills/` (`/my-command` nella TUI di agy).
- `opencode` la collega in `~/.config/opencode/skills/` e nel catalogo condiviso `~/.agents/skills/`, e il sync
  verifica che la skill sia davvero scopribile lì.
- `openclaw` è quello stesso catalogo condiviso: OpenClaw scopre `~/.agents/skills` da solo, quindi nominarlo porta lì
  la skill anche quando nessun altro runtime è nominato.

Tieni un comando portabile: nomi in minuscolo con trattini uguali al nome della cartella, mai il nome di un comando
integrato di una CLI (Claude Code distribuisce un `/doctor` che una skill con lo stesso nome sovrascriverebbe), e un
corpo scritto senza argomenti, "il testo dopo il comando è la richiesta", perché sintassi segnaposto come `$ARGUMENTS`
cambiano da CLI a CLI mentre istruzioni semplici si comportano allo stesso modo ovunque.

Il motore distribuisce alcuni comandi iniziali, registrati in `skills.manifest.yaml.example`. Copia quel file in
`skills.manifest.yaml` nella stessa cartella per averli su un'installazione nuova. Il motore non riscrive mai un
manifest esistente, quindi svuotarlo a `skills: {}` è un'uscita definitiva. I comandi iniziali sono `nexgen-doctor`,
`vault-close`, `vault-save`, `nexgen-council`, `nexgen-discipline`, `vault-groom`, `nexgen-update` e `vault-map`. Ogni
corpo riporta il runbook documentato dello strumento che avvolge, così i flussi protetti restano protetti. I nomi
precedenti `vault-doctor`, `vault-council` e `vault-update` risolvono ancora come alias deprecati.

`/nexgen-update` è la skill pensata per la chat. Il suo lavoro protetto passa dal comando multipiattaforma
`nexgen update` (nome storico `nexgen-update`, ancora installato), quindi i due punti di ingresso condividono un solo
updater.

## Sincronizzazione e migrazione

Cambia il manifest e il corpo sorgente nel Vault, poi committa e pubblica quel cambiamento canonico. Su ogni macchina,
`nexgen guard` (nome storico `agent-sync guard`) riconcilia solo le voci gestite.

Un'installazione esistente può avere ancora vecchie cartelle nelle radici di scoperta. Esegui prima il provisioner
normale, così ripara eventuali link vecchi all'intera radice, poi sposta via le cartelle vecchie con la migrazione
esplicita, una volta sola:

```bash
nexgen guard
nexgen skill sync --migrate-legacy
```

Conserva le cartelle sconosciute sotto `~/.agents/skill-library/legacy/`, fuori dal catalogo e dalle radici dei
runtime. Esse vanno esaminate apposta: promuovi nel manifest bloccato quella che vale, oppure cancellala più avanti con
un consenso esplicito. La guardia ricorrente non esegue mai quella migrazione da sola.

Esegui `nexgen doctor --strict --summary` (nome storico `agent-doctor --strict --summary`) dopo il rollout. Un
cambiamento che attraversa più macchine è completo solo quando hai verificato lo stesso risultato su ogni macchina e
CLI dichiarata.

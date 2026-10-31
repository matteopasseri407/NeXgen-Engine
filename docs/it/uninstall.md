# Disinstallazione

🇬🇧 [English](../uninstall.md)

Non c'è un comando unico che rimuova tutto. La rimozione è manuale, perché ogni scrittura di questo progetto è una
modifica a un file tuo (vedi [what-gets-written.md](what-gets-written.md)), non l'installazione di un pacchetto di
sistema. Questa pagina disfa un pezzo alla volta.

## 1. Fermare il sync ricorrente (profilo MULTI, Linux con systemd)

Ferma i timer e cancella i file unit che questo progetto ha creato sotto `~/.config/systemd/user/`: la guardia del
sync, il battito di liveness e il modello di allarme sui guasti. Niente fuori da quella cartella viene toccato, e
rieseguire il setup MULTI li ricrea:

```bash
systemctl --user disable --now agent-sync.timer agent-heartbeat.timer
rm -f ~/.config/systemd/user/agent-sync.service ~/.config/systemd/user/agent-sync.timer \
      ~/.config/systemd/user/agent-heartbeat.service ~/.config/systemd/user/agent-heartbeat.timer \
      ~/.config/systemd/user/agent-alert@.service
systemctl --user daemon-reload
```

Su Windows il provisioner MULTI registra attività pianificate con `schtasks.exe`, appoggiate allo shim installato
`~/.local/bin/agent-sync.cmd` dietro un wrapper `.vbs` nascosto, al posto delle unit systemd. Rimuovile con:

```powershell
schtasks /Delete /TN "KnowledgeVault Agent Sync" /F
schtasks /Delete /TN "KnowledgeVault Agent Sync Logon" /F
schtasks /Delete /TN "KnowledgeVault Agent Heartbeat" /F
```

## 2. Ripristinare la configurazione per CLI dai backup, o modificarla a mano

Prima di sovrascrivere un file che gestisce, `nexgen sync` o `nexgen guard` ne salva la versione precedente accanto.
Le convenzioni di nome sono due, quindi cerca entrambe: il renderer MCP (`nexgen_core/renderer.py`) scrive
`<file>.bak-<timestamp>` per ogni configurazione MCP per CLI, mentre il ciclo di guardia e gli scrittori di permessi e
guardrail per CLI scrivono `<file>.pre-<reason>-<timestamp>.bak` per i file che riscrivono. Cerca accanto a:

- `~/.claude.json` (le voci `mcpServers` aggiunte da questo progetto), `.bak-<timestamp>`
- configurazione in `~/.codex/`, `.bak-<timestamp>`
- `opencode.json` o `opencode.jsonc` (la sezione `mcp.servers`, le voci `plugins` e `permissions`),
  `.bak-<timestamp>`
- `~/.gemini/antigravity/mcp_config.json`, `.bak-<timestamp>`
- `~/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/config/AGENTS.md`, `~/.config/opencode/AGENTS.md` (il puntatore
  alle istruzioni), `.pre-instructions-<timestamp>.bak`
- `~/.claude/settings.json` (l'hook di checkpoint e, se il tuo Vault ne dichiarava uno, l'assetto dei permessi),
  `.pre-permissions-<timestamp>.bak`

I backup MCP e quelli dei permessi tengono i tre più recenti, quindi la copia di prima del primo avvio può essere già
sparita. I backup dei puntatori alle istruzioni non ruotano e si accumulano.

Se esiste un file `.bak` precedente al primo intervento di questo progetto, ripristinalo. Altrimenti togli solo le
voci MCP e il puntatore `instructions` o di bootstrap aggiunti da questo progetto; la tabella in
[what-gets-written.md](what-gets-written.md) dice quale campo guardare per ogni CLI.

## 3. Rimuovere i file puntatore di bootstrap

Sono i file di bootstrap per CLI che questo progetto puntava alle sue istruzioni. Rimuovili solo se non li usi anche
per altro: cancellarne uno elimina il tuo contenuto nello stesso file, non solo il puntatore di questo progetto.

```bash
rm -f ~/CLAUDE.md ~/.codex/AGENTS.md ~/.gemini/config/AGENTS.md
```

## 4. Rimuovere le cartelle delle skill copiate da questo progetto

Non esiste un elenco fisso. Quali skill esistono dipende dal tuo `skills.manifest.yaml`, che è dato e non parte del
motore; un'installazione nuova non ne ha nessuna finché non ne aggiungi. Guarda il catalogo, la libreria e la cartella
di runtime di ogni CLI, e rimuovi solo ciò che riconosci come proveniente dal tuo manifest:

```bash
cat ~/.agents/skills/INDEX.md       # catalogo gestito, non radice dei corpi delle skill
ls ~/.agents/skill-library           # corpi delle skill gestite
rm -rf ~/.agents/skill-library/<skill-name>
rm -rf ~/.claude/skills/<skill-name> ~/.codex/skills/<skill-name> ~/.gemini/antigravity-cli/skills/<skill-name>
```

`nexgen skill index` rigenera `~/.agents/skills/INDEX.md` (nome storico `skills-sync.py --index`). Non cancellare
l'intera cartella `~/.agents/skills/` se un altro strumento usa il suo catalogo; rimuovi solo le cartelle gestite che
riconosci.

## 5. Modalità Local-Full: smontare lo stack Docker locale

Fallo prima del passo 6, perché i file compose che servono stanno nel clone del Vault che stai per cancellare. Se hai
eseguito i connettori sul Docker di questa macchina (`nexgen stack up`), `nexgen stack down` li ferma e lascia intatti
i volumi dei dati. Per rimuovere anche quelli, salva prima ciò che vuoi tenere (workflow n8n, stato OCR), poi esegui
`docker compose down -v` tu stesso dentro la cartella di ogni stack sotto `03-INFRA/deploy/<service>/`.

## 6. Rimuovere il clone del Vault

Questo è il passo distruttivo. `~/KnowledgeVault` contiene il motore e tutto ciò che ci hai scritto: note, progetti e
`99-INDEX/USER-PROFILE.md`. Cancellarlo cancella tutto insieme, senza possibilità di tornare indietro se non hai un
tuo backup o un remoto Git separato. Salva ciò che vuoi tenere sotto `01-NOTES/`, `02-PROJECTS/` o `04-NOW/`, poi:

```bash
rm -rf ~/KnowledgeVault   # o dove l'hai clonato
```

## 7. Modalità Cloud-Server: smontare lo stack della VPS a parte

Se hai distribuito `03-INFRA/deploy/` (n8n, OCR, vault-mcp) su una VPS, quello è uno stack Docker Compose separato su
una macchina separata. Niente qui lo raggiunge via rete, quindi lo smonti tu. Salva prima ciò che vuoi tenere, perché
`docker compose down -v` nella cartella di quello stack cancella i suoi container e i suoi volumi.

## 8. Stato e segreti che restano

- `~/.nexgen-engine/`: la directory di stato locale della macchina (file di lock del sync, timestamp di liveness, cache
  del doctor, report del controllo dipendenze). Si può cancellare, è testo semplice, senza segreti.
- `99-SECRETS/`: conta solo se stava nel clone rimosso al passo 6. Un archivio GPG copiato altrove resta intatto.

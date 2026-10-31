# Il servizio gateway di OpenClaw: architettura e operazioni

🇬🇧 [English](../openclaw-service.md)

OpenClaw fa da harness di esecuzione per NeXgen Engine. Il suo demone gateway tiene le sessioni runtime in background, collega i provider di strumenti configurati e ascolta sulla porta `18789` per impostazione predefinita. La release fissata è quella in `03-INFRA/agent-universal-layer/openclaw/pin.json`.

Questa pagina copre l'architettura del servizio sulle due piattaforme, i comportamenti di Windows 11 verificati sul campo, le trappole note e il flusso di accoppiamento implementato in `nexgen_core.openclaw_service` e `nexgen_core.openclaw_pairing`.

## Meccanica del servizio per piattaforma

La cartella di stato del gateway si risolve in questo ordine: la cartella che contiene `$OPENCLAW_CONFIG_PATH`, poi `$OPENCLAW_STATE_DIR`, poi `~/.openclaw`.

### Windows 11

`openclaw gateway install` genera `<state dir>/gateway.cmd`, che avvia:

```cmd
node <openclaw_dir>\dist\index.js gateway --port 18789 --task-supervisor
```

L'avvio automatico funziona in due modi:

1. Task Scheduler: un'attività chiamata `"OpenClaw Gateway"`, interrogata con `schtasks /Query /TN "OpenClaw Gateway"` e avviata con `schtasks /Run /TN "OpenClaw Gateway"`.
2. Riserva con VBS nella cartella Esecuzione automatica: quando Task Scheduler non è disponibile o è disattivato, uno script `OpenClaw Gateway.vbs` va nella cartella Esecuzione automatica dell'utente (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`) e lancia `gateway.cmd` in modo invisibile tramite `wscript.exe`.

### Linux

OpenClaw registra un'unità systemd utente in `~/.config/systemd/user/openclaw-gateway.service`, gestita con i soliti comandi della sessione utente:

```bash
systemctl --user status|start|stop|restart openclaw-gateway.service
```

## Trappole di Windows 11 e rimedi verificati

`openclaw gateway restart` non è affidabile. Su Windows spesso si blocca o lascia processi figli orfani, e due gateway finiscono per contendersi lo stesso socket. `nexgen_core.openclaw_service` esegue invece un ciclo pulito di terminazione e rilancio:

1. Enumera i processi per trovare ogni istanza `node` che esegue `openclaw` e `dist\index.js gateway`, più ogni `cmd.exe` che esegue `gateway.cmd`.
2. Termina l'albero dei processi con `taskkill.exe /PID <pid> /T /F`.
3. Rilancia tramite `wscript.exe "<startup>\OpenClaw Gateway.vbs"`, oppure `schtasks /Run /TN "OpenClaw Gateway"` quando l'attività esiste.

Un avvio a freddo del gateway può richiedere fino a cinque minuti prima che la porta `18789` accetti connessioni. `restart(wait=True, timeout_s=600.0)` interroga la porta TCP su loopback con un timeout predefinito di dieci minuti, riporta i secondi trascorsi quando è pronto e restituisce un errore di timeout quando non lo è.

Modificare il blocco `plugins` di `openclaw.json` mentre il gateway gira corrompe lo stato in esecuzione e le sottoscrizioni MCP attive. Usa il context manager `stopped()` ogni volta che modifichi la configurazione:

```python
from nexgen_core.openclaw_service import OpenClawService

service = OpenClawService()
with service.stopped():
    # Modifica in sicurezza i plugin o le impostazioni MCP di openclaw.json
    update_openclaw_configuration()
# Il gateway viene riavviato nel blocco finally, anche se è stata sollevata un'eccezione
```

L'app OpenClaw Companion presa dal Microsoft Store installa un secondo gateway come pacchetto MSIX sotto `%LOCALAPPDATA%\Packages` e lascia un collegamento `OpenClaw Companion.lnk` nella cartella Esecuzione automatica. Con entrambi attivi, i due continuano a contendersi la porta `18789`. `OpenClawService.status()` ispeziona `%LOCALAPPDATA%\Packages` e la cartella Esecuzione automatica; quando trova l'app imposta `companion_present: True` e suggerisce il comando di rimozione:

```powershell
Get-AppxPackage *OpenClaw* | Remove-AppxPackage
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\OpenClaw Companion.lnk" -Force
```

Avvii multipli accidentali possono lasciare due o più processi gateway in esecuzione. `status()` riporta `dual_gateway: True` quando `process_count > 1`, o quando l'app Companion dello Store è in conflitto con un gateway attivo.

## Protocollo di accoppiamento dei dispositivi

I telefoni con l'app companion di OpenClaw si accoppiano al gateway locale in tre passi:

1. Mostrare il codice QR. `pairing.qr()` restituisce il comando `openclaw qr`. Il codice va disegnato nel terminale interattivo della persona, non catturato sullo standard output.
2. Ispezionare gli accoppiamenti in attesa. `pairing.pending()` esegue `openclaw devices list --json`, con il parsing del testo come riserva. Ogni token di autenticazione, segreto e chiave di credenziale viene eliminato prima che i record vengano restituiti.
3. Approvare l'accoppiamento. `pairing.approve(device_id)` approva la registrazione in attesa con `openclaw devices approve <device_id>`.

Una nota sul trasporto: una connessione WebSocket non sicura (`ws://`) dà capacità limitate.

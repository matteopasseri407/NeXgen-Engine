# Distribuzione org / backend condiviso

🇬🇧 [English](../org-deployment.md)

Per chi valuta NeXgen Engine come infrastruttura per più di una persona, cioè il caso in cui più persone condividono
un unico backend Cloud-Server (una VPS che esegue n8n, OCR e ricerca semantica). Leggilo prima di collegare la CLI di
una seconda persona allo stesso backend. Per i limiti mono-utente del Vault (un profilo, una passphrase dei segreti,
nessun confine per persona) vedi [team.md](team.md); qui si parla solo del backend condiviso.

## Cosa è supportato oggi

Un Vault per persona. Ognuno usa il proprio clone, risponde alla propria intervista `INIT.md` e, in modalità
Cloud-Server, lo punta a infrastruttura che controlla da solo. Nulla supporta più persone che condividono un backend
Cloud-Server o un Vault come se avesse confini per persona.

## Il rischio della VPS condivisa

Se un'organizzazione mette in piedi un unico backend Cloud-Server e lascia che le CLI di più dipendenti lo
raggiungano dai propri tunnel SSH, ognuna di quelle CLI ottiene accesso completo in lettura e scrittura a tutto ciò
che il backend espone.

`SECURITY.md` dice di un singolo Vault: "Any agent CLI with filesystem access to it can read and write everything
inside. There is no per-file access control beyond your OS permissions." Vale identico per una VPS condivisa: qui
niente aggiunge separazione per utente ai workflow n8n, ai servizi OCR e di ricerca semantica, né a un Vault
rispecchiato attraverso quel backend. La CLI di un dipendente può leggere le note di un altro e, se da lì si
raggiungono l'archivio o il registro dei segreti, anche i segreti di un altro.

Non c'è un'impostazione che lo sistemi. Un backend Cloud-Server condiviso tra persone significa fiducia reciproca
piena tra tutti quelli la cui CLI lo raggiunge, come dare a tutti la stessa password di root. Farlo è a tuo rischio, e
quando diventa lavoro pagato o che genera ricavi dentro un'azienda è un uso commerciale che la licenza non copre e per
cui non esiste una licenza commerciale (vedi `COMMERCIAL.md`).

## Uno schema di partenza per un rollout IT

È uno schema di partenza, non uno strumento di rollout: niente nel repo lo automatizza e nessuno l'ha provato su una
scala reale con più persone.

Per N persone che vogliono setup di agenti governati senza condividere un backend, la forma a rischio più basso oggi
sono N installazioni MINIMAL indipendenti:

1. **Un clone del Vault per persona.** Clona il repo in una cartella propria, o su una macchina propria, per ciascuno.
   Ogni clone è il suo confine di fiducia, e nessuna CLI ha un percorso dentro il clone di un altro.
2. **Un `99-INDEX/USER-PROFILE.md` compilato per persona.** Esegui `INIT.md` separatamente per ogni clone, così ognuno
   risponde alla propria intervista: stesso modello, il proprio hardware, la propria scelta di CLI, la propria
   decisione di architettura.
3. **Nessuna VPS condivisa.** Se qualcuno ha bisogno della modalità Cloud-Server, dagli infrastruttura che controlla
   solo lui: una VPS propria, oppure un account o namespace isolato creato in modo che il suo tunnel e i suoi segreti
   non raggiungano quelli di altri. Puntare due cloni alla stessa VPS ricrea il rischio descritto sopra.
4. **Una checklist breve per persona:** la propria archiviazione di credenziali per macchina (keyring del sistema,
   variabili d'ambiente), mai condivisa con nessuno, IT compreso; `99-SECRETS/` come spazio locale ignorato da git in
   cui è tracciato solo il registro non sensibile; il proprio `USER-PROFILE.md` compilato; e la conferma che i server
   MCP della propria CLI puntano solo a infrastruttura che controlla da solo.

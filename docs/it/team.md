# Uso in team oggi

🇬🇧 [English](../team.md)

Questa pagina è per chi valuta NeXgen Engine per più di una persona: due colleghi, o una piccola azienda.
Leggila prima di adottarlo come infrastruttura condivisa. Il modello di sicurezza e di identità oggi è mono-utente per scelta, e questo ha conseguenze concrete per un team.

La questione della licenza è separata e resta valida. NeXgen Engine è PolyForm Noncommercial 1.0.0: valutarlo internamente va bene, mentre usarlo dentro un'azienda come parte di un lavoro pagato o che genera ricavi è un uso commerciale che la licenza predefinita non copre.
Non esiste una licenza commerciale (vedi [COMMERCIAL.md](../../COMMERCIAL.md)), quindi un uso del genere non è licenziato, qualunque conclusione tu tragga sui limiti mono-utente qui sotto.

## Cosa significa "mono-utente", in concreto

- **Un profilo, non uno per persona.** `99-INDEX/USER-PROFILE.md` contiene una sola descrizione di hardware e host e una sola configurazione della squadra di modelli per tutto il vault.
  Il framework non ha il concetto di "chi del team sta guidando adesso": c'è un file, e vince chi l'ha modificato per ultimo.
- **Non viene distribuito nessun archivio di segreti condiviso.** `99-SECRETS/` è uno spazio locale ignorato da git; sono tracciati solo `README.md` e `secrets-registry.md`, e il registro non contiene mai valori.
  Le credenziali vere restano dove le tiene ogni macchina: keyring del sistema operativo, variabili d'ambiente dell'utente, archivi di ogni CLI.
  Non c'è separazione delle credenziali per persona. Condividere un clone condivide configurazione e note, mai credenziali, e non c'è modo di dare a un collega accesso in lettura al token n8n senza consegnargli anche la chiave del servizio OCR.
- **Nessun controllo di accesso per file oltre a quello del sistema operativo.** Come dice [SECURITY.md](../../SECURITY.it.md): "Qualunque CLI agente con accesso al filesystem [del vault] può leggere e scrivere tutto quello che c'è dentro."
  È vero per un utente singolo ed è altrettanto vero quando tre persone condividono un clone: niente nel framework impedisce all'agente di un collega di modificare le note di un altro, il profilo, o il registro condiviso dei segreti.
- **Nessuna separazione dei ruoli e nessuna traccia di audit per persona.** Non esistono un amministratore e un membro normale, e non c'è un registro integrato di chi ha fatto una modifica, inteso come persona e non come macchina, oltre alla paternità che il tuo `git commit` produce.

## Cosa comporta se un piccolo team lo adotta oggi

- **Condividere un solo clone del vault**, la strada più corta per "tutti vedono le stesse note", mette tutti nello stesso dominio di fiducia: un profilo condiviso, un confine di fiducia condiviso, nessuna separazione delle credenziali.
  Va bene per due persone che si fidano già di tutto l'uno dell'altro. Non va bene per un team che vuole compartimentare chi vede cosa.
- **Dare a ciascuno un clone separato** evita di condividere le credenziali, ma allora note, profilo e routing dei modelli non sono più condivisi automaticamente. La base di conoscenza del team sparisce e restano N installazioni mono-utente indipendenti che usano lo stesso framework.
- Il profilo di installazione `MULTI` (vedi [INIT.md](../../INIT.md), passo 1) non risolve la cosa: tiene in sincrono le CLI e le macchine di una persona, non le identità di più persone con un vault condiviso.
  Il nome suggerisce altro, quindi vale la pena dirlo chiaro.

La maggior parte dei piccoli team finisce tra queste due opzioni e accetta il compromesso in modo esplicito, invece di ricevere una risposta pulita dal framework.

## Cosa non c'è

In questa release il supporto multi-utente vero non è implementato: identità per persona, segreti con ambito ristretto, un modello di ruoli e permessi, audit per persona.
È un lavoro separato e più grande, già previsto, e non qualcosa da aggiungere come patch di documentazione.
Questa pagina esiste perché tu possa decidere se NeXgen Engine va bene per un team oggi, con i vincoli di oggi, invece di scoprirli dopo.

## Un aiuto dichiarativo al routing non è supporto multi-utente

`99-INDEX/USER-PROFILE.md` ha una sezione facoltativa "Team members" che dichiara chi, oltre al proprietario del vault, usa il framework. Serve perché il `seats.yaml` del Council non debba essere un file che tutti modificano insieme, e perché una skill possa essere marcata `scope: personal` così che `nexgen skill sync` (nome storico `skills-sync.py`) la metta solo sulla macchina del suo proprietario.
Fa solo questo: instrada sedute e skill, dicendo chi possiede quale host, quale file delle sedute, quali skill.
Non è un confine di sicurezza, e tutti i vincoli qui sopra valgono ancora.
Resta un solo file di profilo in cui vince l'ultima modifica, un solo archivio dei segreti con una chiave per macchina iscritta, nessun controllo di accesso indipendente dal sistema operativo e nessuna traccia di audit per persona.
Dichiarare i membri del team rende più comodo il routing di sedute e skill; non aggiunge identità, isolamento o permessi.

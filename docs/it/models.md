# Tier di instradamento e gestione modelli: `nexgen models`

🇬🇧 [English](../models.md)

Il supervisore ("sup") seleziona i modelli in base a tier espliciti e disponibilità, abbandonando i pattern glob.
I tier sono definiti in `03-INFRA/agent-universal-layer/routing-tiers.yaml` nel KnowledgeVault, con un modello di riferimento in `03-INFRA/agent-universal-layer/templates/routing-tiers.yaml.example`.

## Comandi

```bash
nexgen models list                   # mostra tier, candidati e stato di esaurimento
nexgen models pick <tier> [--limited] # seleziona il primo candidato disponibile
nexgen models trial <modello> --harness <harness> # esegue un test deterministico su codice
nexgen models promote <modello>      # promuove il candidato in cima al tier worker
nexgen models rollback               # ripristina l'ordine precedente dei worker
nexgen models discover               # scopre nuovi modelli Flash idonei dall'inventario
```

## Struttura dei tier

- `tiers.orchestrator`: modelli frontier flat per la pianificazione dei compiti.
- `tiers.worker`: candidati flat per l'esecuzione, con `model`, `harness` e volume `requests_5h`.
- `tiers.limited`: modelli gratuiti o contributor per compiti non sensibili.

## Vincoli di sicurezza per il tier limited

Un compito non contrassegnato è considerato sensibile: il tier limited non viene mai selezionato a meno che non sia specificato il flag `--limited`.
Il tier limited non deve mai ricevere dati finanziari, posta, identità o segreti. Il codice sorgente non è considerato dato sensibile.

## Rilancio automatico in `sup status`

Quando `sup status` trova un'unità aperta la cui sessione ha esaurito la quota o si è fermata, rilancia lo stesso brief in una sessione nuova sul candidato successivo, una sola volta per ogni sessione bloccata. Un modello flat non passa mai al tier limited.

## Test deterministico (`trial`)

`nexgen models trial` verifica un modello su un piccolo progetto Python isolato in Git con un test che fallisce. Il test passa solo se il modello corregge il codice, fa passare il test entro il tempo limite e non modifica altri file.

## Rilevamento automatico (`discover`)

`nexgen models discover` è un comando manuale. La prima esecuzione registra solo l'inventario attuale in `known_models` nello stato locale: non esegue trial e non manda avvisi. Le esecuzioni successive guardano solo i modelli mai visti, sui canali flat (`flat_prefixes`, predefinito `opencode-go/`; i nomi con "free" o "contributor" sono esclusi, e i modelli Zen `opencode/` non vengono considerati). Ogni nuovo modello Flash con volume sufficiente in `channels_volume` viene provato con `trial`. L'esito (superato o fallito, con il motivo) arriva come avviso Megaphone: discover non promuove mai. Per adottare un modello superato esegui `nexgen models promote <modello>`; per annullare, `nexgen models rollback`. Un modello nuovo con volume ignoto genera un solo avviso che chiede il numero in `channels_volume`. Sulla macchina con ruolo server il beat esegue discover al massimo una volta ogni 24 ore. Un modello viene provato una sola volta, quindi un rollback non viene annullato dalla esecuzione successiva.

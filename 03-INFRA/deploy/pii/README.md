# PII — sidecar anonimizzatore (Rizzo PII)

Anonimizzazione PII italiana via modello 0.3B + rete deterministica,
basata su **Rizzo PII di Rizzo-AI-Academy (licenza MIT)**:
<https://github.com/Rizzo-AI-Academy/rizzo-pii> —
modello <https://huggingface.co/rizzoaiacademy/rizzo-pii-0.3B>.
Niente codice upstream nel repo: `build-pii.sh` clona il tag pinnato
(`v2.0.0`, pin corto verificato in `build-pii.sh`) e compila, modello incluso nell'immagine.

## Risorse misurate (VPS ARM 2 core, 2026-10-11)

1 worker, ~1 GB RAM, testo breve in ~0.5 s a modello caldo.
`mem_limit: 2.5g`, `cpus: 1.5`, volume solo per preferenze UI.

## Rete

Il container ascolta su `127.0.0.1:5005`, mai esposto. Le CLI lo
raggiungono via tunnel: aggiungere alla riga SSH del deploy README
`-L 127.0.0.1:<pii-tunnel-port>:127.0.0.1:5005`.

## MCP e fallback

`mcp/rizzo_pii_mcp.py` (solo stdlib): tool `pii_anonymize`. Chiama il
sidecar; se non risponde avvia l'AppImage locale
(`PII_LOCAL_APP`, default in `~/Applications`) sulla `PII_LOCAL_PORT`
e riprova. Il fallback è locale e irreversibile solo se chiesto
(`mapping: false`).

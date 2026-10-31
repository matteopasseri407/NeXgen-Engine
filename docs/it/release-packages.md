# Canali di distribuzione (pacchetti di release)

🇬🇧 [English](../release-packages.md)

Cosa pubblica una release taggata e firmata, cosa parte da solo e cosa richiede una decisione del maintainer. Ogni
affermazione qui sotto è imposta da un passo del workflow oppure è dichiarata come lacuna.

## Pubblicato a ogni release (nessun segreto richiesto)

Ogni tag produce tre asset:

- `nexgen_engine-X.Y.Z.tar.gz`, l'sdist che installano pipx e uv
- `nexgen_engine-X.Y.Z-py3-none-any.whl`, la wheel
- `SHA256SUMS`, lo sha256 di ogni asset, così qualsiasi installer può verificare

Sono l'unica fonte di byte per i canali a valle: la formula Homebrew punta all'asset sdist e ne porta lo sha256, e
PyPI riceve gli stessi file. Un artefatto, un hash, verificato ovunque.

## Tap Homebrew (automatico, richiede una configurazione iniziale)

Il passo "Update the Homebrew tap" gira a ogni release e spinge una `Formula/nexgen.rb` generata verso
`matteopasseri407/homebrew-nexgen`. È vincolato al segreto `HOMEBREW_TAP_TOKEN`: finché quel segreto non esiste, il
passo appare come skip visibile e non fa fallire la release.

Configurazione, una volta sola:

1. Crea il repository pubblico `homebrew-nexgen` (anche vuoto va bene; contiene solo formule generate).
2. Crea un token fine-grained limitato a quel repository con il solo permesso `contents: read/write` e registralo come
   segreto `HOMEBREW_TAP_TOKEN` su NeXgen-Engine.
3. La release firmata successiva genera la formula e la spinge. Il generatore è
   `03-INFRA/scripts/release_packages.py`, che fissa come risorsa Homebrew ogni dipendenza dichiarata dal pacchetto
   (quelle a runtime e il build backend), al digest che PyPI dichiara al momento della build.
4. Lacuna nota: la formula porta le dipendenze dichiarate, non la loro chiusura transitiva, che Homebrew di solito
   risolve con `brew update-python-resources`. Nessuno ha mai installato dal tap, perché il passo del workflow è uno
   skip finché il token non esiste.

Poi: `brew install matteopasseri407/nexgen/nexgen`.

## PyPI (automatico, richiede una configurazione iniziale)

Registra un token API di PyPI come segreto `PYPI_API_TOKEN` e il passo "Publish to PyPI" carica gli stessi sdist e
wheel a ogni release. Fino a quel momento `uv tool install nexgen-engine` non risolve niente, ed è per questo che il
README installa dall'URL git.

## Windows: cosa esiste, cosa è una decisione

- `install.ps1 -Check` nella radice del repo è la strada Windows di oggi, versionata insieme al repo.
- La firma Authenticode è una lacuna nota: richiede un certificato di code signing (uno OV costa circa 100-400 €
  all'anno, uno EV di più) e una decisione personale su quale identità firma. Finché non succede, nel repo niente
  finge di essere firmato.
- I manifest `winget` e `scoop` richiedono un artefatto di installazione vero (un exe portabile o un MSI), non un
  sdist Python. È una decisione di packaging, PyInstaller in file singolo oppure MSI, deliberatamente non presa qui.

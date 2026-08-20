#!/usr/bin/env sh
# NeXgen Engine — bootstrap.
#
# Questo file fa una cosa sola: trovare Python e passargli il testimone.
# Tutto il resto — prerequisiti, struttura del vault, assistenti trovati,
# profilo consigliato — vive in 03-INFRA/scripts/nexgen_core/bootstrap.py,
# una volta sola, perché non si può verificare con Python che Python esista
# ma tutto ciò che viene dopo sì.
#
#   sh install.sh            preflight, domande e passo successivo
#   sh install.sh --check    solo controlli, nessuna domanda e nessuna scrittura
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
BOOTSTRAP="$ROOT/03-INFRA/scripts/nexgen_core/bootstrap.py"

if [ ! -f "$BOOTSTRAP" ]; then
    echo "NeXgen: questo clone è incompleto, manca $BOOTSTRAP" >&2
    exit 1
fi

for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            exec "$candidate" "$BOOTSTRAP" "$@"
        fi
        FOUND="$("$candidate" --version 2>&1)"
    fi
done

echo "NeXgen: serve Python 3.11 o successivo.${FOUND:+ Trovato: $FOUND.}" >&2
echo "Installalo dal gestore di pacchetti del tuo sistema e rilancia questo script." >&2
exit 1

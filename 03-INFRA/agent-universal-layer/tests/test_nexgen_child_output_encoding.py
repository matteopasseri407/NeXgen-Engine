"""Un figlio si legge in UTF-8 con sostituzione, mai con il codec della macchina.

`text=True` da solo decodifica con la codifica della locale (cp1252 su un
Windows occidentale) e, con l'output catturato, quella decodifica avviene nel
thread lettore di subprocess: un byte che il codec non definisce, per esempio
un messaggio git localizzato, diventa un UnicodeDecodeError in un thread invece
di un errore leggibile. Il chiamante riceve un output vuoto al posto del testo,
e il difetto si vede solo sotto carico, sulla macchina dove la locale italiana
decodifica davvero male.

Questa prova non guarda un punto di chiamata: fissa la regola per tutto il
motore, così il prossimo `subprocess.run(..., text=True)` non riporta il
difetto in un'altra corsia.
"""
from __future__ import annotations

import ast
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
ENGINE_DIR = SCRIPTS_DIR / "nexgen_core"

#: Le funzioni che leggono l'output di un processo.
_SPAWNING_CALLS = frozenset({"run", "Popen", "check_output", "check_call", "call"})

#: Ciò che accende la modalità testo in quelle chiamate.
_TEXT_KEYWORDS = ("text", "universal_newlines", "encoding")


def _literal(node: ast.AST | None):
    return node.value if isinstance(node, ast.Constant) else None


def _text_mode_call(node: ast.Call) -> bool:
    """True when this call asks subprocess for text, so a codec will decode."""
    keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
    if "encoding" in keywords:
        return True
    return any(_literal(keywords.get(keyword)) is True for keyword in _TEXT_KEYWORDS)


def _encoded_safely(node: ast.Call) -> bool:
    """UTF-8 with replacement: no byte can raise, whatever the machine's locale."""
    keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
    return _literal(keywords.get("encoding")) == "utf-8" and _literal(keywords.get("errors")) == "replace"


def test_every_text_mode_child_read_uses_utf8_with_replacement():
    offenders: list[str] = []
    for path in sorted(ENGINE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
            if name not in _SPAWNING_CALLS or not _text_mode_call(node):
                continue
            if not _encoded_safely(node):
                offenders.append(f"{path.relative_to(SCRIPTS_DIR)}:{node.lineno}")

    assert offenders == [], (
        "un figlio si legge con encoding=\"utf-8\", errors=\"replace\": " + ", ".join(offenders)
    )

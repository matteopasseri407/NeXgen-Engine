"""Text read, written or captured by the engine names its encoding.

Without one Python uses the machine's locale. On Windows with an Italian
locale that is cp1252: a note called "perché.md" listed by git comes back as
mojibake, and a byte cp1252 has no character for aborts the read. Linux
hides it (the locale is UTF-8), so nothing in a Linux test run ever shows
the problem; this scan is what keeps it from coming back one call at a time.
"""
from __future__ import annotations

import ast
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2]
SKIPPED_PARTS = {".venv", "node_modules", "__pycache__", "tests"}


def _is_binary_open(call: ast.Call) -> bool:
    # open(path, mode) names the file first; Path.open(mode) does not.
    index = 1 if isinstance(call.func, ast.Name) else 0
    mode = call.args[index].value if len(call.args) > index and isinstance(call.args[index], ast.Constant) else None
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and "b" in mode


def _violations(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {kw.arg: kw for kw in node.keywords}
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("read_text", "write_text"):
            positional_encoding = node.args[:1] if func.attr == "read_text" else node.args[1:2]
            if "encoding" not in keywords and not positional_encoding:
                found.append((node.lineno, func.attr))
        elif isinstance(func, ast.Name) and func.id == "open" or (
            isinstance(func, ast.Attribute) and func.attr == "open"
            and isinstance(func.value, (ast.Name, ast.Attribute, ast.Call))
            and not (isinstance(func.value, ast.Name) and func.value.id in {"Image", "os", "webbrowser", "opener"})
        ):
            if not _is_binary_open(node) and "encoding" not in keywords:
                found.append((node.lineno, "open"))
        text_flag = keywords.get("text") or keywords.get("universal_newlines")
        if text_flag is not None and isinstance(text_flag.value, ast.Constant) and text_flag.value.value is True:
            if "encoding" not in keywords:
                found.append((node.lineno, "subprocess text=True"))
    return found


def test_every_text_io_call_names_its_encoding():
    offenders = []
    for path in sorted(INFRA.rglob("*.py")):
        if SKIPPED_PARTS & set(path.relative_to(INFRA).parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [f"{path.relative_to(INFRA)}:{line} {what}" for line, what in _violations(tree)]
    assert not offenders, "text IO without an explicit encoding:\n" + "\n".join(offenders)


def test_the_scan_catches_what_it_is_for():
    source = (
        "from pathlib import Path\nimport subprocess\n"
        "Path('a').read_text()\nPath('a').write_text('x')\nopen('f')\n"
        "subprocess.run(['x'], text=True)\n"
    )
    assert [what for _, what in _violations(ast.parse(source))] == [
        "read_text", "write_text", "open", "subprocess text=True"]
    fine = (
        "from pathlib import Path\nimport subprocess\n"
        "Path('a').read_text('utf-8')\nPath('a').write_text('x', 'utf-8')\nopen('f', 'rb')\n"
        "Path('a').open('rb')\nsubprocess.run(['x'], text=True, encoding='utf-8')\n"
    )
    assert _violations(ast.parse(fine)) == []

"""Whether one resource OpenClaw created may enter the library, and who has to say yes.

The canonical library is the single source every CLI receives, so what enters
it is worth one honest look. The checks are the engine's own, not new ones: a
skill name is a folder name (`is_safe_skill_name`), a server name is a manifest
key (`SERVER_NAME_RE`), a credential is what `secret_shapes` recognises, and
"this file can run" is `thirdparty_guard`'s definition.

One job: turn a candidate into a verdict -- `SAFE` to adopt, `REVIEW` when a
person has to look first, `BLOCKED` when the engine must never write it.
Nothing here writes, and nothing here prints a value it found suspicious.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from nexgen_core import secret_shapes
from nexgen_core.i18n import t
from nexgen_core.mcp_add import SERVER_NAME_RE
from nexgen_core.openclaw.adopt_scan import UnmanagedServer, WorkshopSkill
from nexgen_core.skill_sources import is_safe_skill_name
from nexgen_core.thirdparty_guard import (
    HOOK_NAMES,
    PROSE_SUFFIXES,
    SCRIPT_SUFFIXES,
)

SAFE = "safe"
REVIEW = "review"
BLOCKED = "blocked"

#: OpenClaw's own ceiling for the file that carries the instructions
#: (`skills.workshop.maxSkillBytes`, default 40000).
MAX_SKILL_MD_BYTES = 40_000
#: OpenClaw's ceiling for one support file under `references/`, `scripts/`, `assets/`.
MAX_SUPPORT_BYTES = 256 * 1024
#: What a skill may weigh in total before it is something else wearing a skill's shape.
MAX_SKILL_BYTES = 2 * 1024 * 1024
#: A skill is a handful of files, not a tree.
MAX_SKILL_FILES = 200

#: Suffixes that cannot do anything when a CLI reads them. Anything else is
#: held for a look: an unknown file is not guilty, it is unknown.
INERT_SUFFIXES = frozenset(PROSE_SUFFIXES | {".json", ".yaml", ".yml", ".toml", ".csv", ".tsv"})
#: Of those, the ones that are text: only text can carry a credential, and
#: reading a picture as text would invent one out of its bytes.
TEXT_SUFFIXES = frozenset({".md", ".mdx", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv"})
#: A skill description is 1-1024 bytes, in OpenClaw's own frontmatter contract.
MAX_DESCRIPTION_BYTES = 1024

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass(frozen=True)
class Verdict:
    """The answer about one candidate, with the reasons a person can act on."""

    status: str
    reasons: tuple[str, ...] = ()

    @property
    def adoptable(self) -> bool:
        return self.status == SAFE

    def joined(self) -> str:
        return "; ".join(self.reasons)


def frontmatter(path: Path) -> dict[str, Any] | None:
    """The YAML frontmatter of a `SKILL.md`, or None when it cannot be read as one."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):  # nexgen: allow reason="an unreadable SKILL.md is reported as 'no frontmatter', never raised at a person"
        return None
    match = _FRONTMATTER.match(text)
    if not match:
        return None
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError:  # nexgen: allow reason="broken frontmatter is a verdict, not a crash: the file is refused, not parsed"
        return None
    return loaded if isinstance(loaded, Mapping) else None


def skill_description(path: Path) -> str:
    """The one-line description a manifest entry carries, from the skill itself."""
    data = frontmatter(path / "SKILL.md") or {}
    return " ".join(str(data.get("description") or "").split())


def _is_executable(path: Path) -> bool:
    """Whether the file itself can run here, whatever its name says."""
    try:
        return bool(path.stat().st_mode & 0o111)
    except OSError:  # nexgen: allow reason="a file that vanished mid-scan is reported by the size check that follows"
        return False


def _files(root: Path) -> list[Path]:
    """Every file under the skill folder; symlinked folders are not followed."""
    return sorted(path for path in root.rglob("*") if path.is_file() or path.is_symlink())


def _size_problems(root: Path, files: list[Path]) -> list[str]:
    """What is too big to be one skill, in total or in one file."""
    problems: list[str] = []
    if len(files) > MAX_SKILL_FILES:
        problems.append(t("{count} files: a skill is a handful, this is a tree", count=len(files)))
    total = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:  # nexgen: allow reason="a file removed during the scan cannot be measured; the copy will report it"
            continue
        total += size
        limit = MAX_SKILL_MD_BYTES if path.name == "SKILL.md" else MAX_SUPPORT_BYTES
        if size > limit:
            problems.append(t("{name} weighs {size} bytes (limit {limit})", name=path.name, size=size, limit=limit))
    if total > MAX_SKILL_BYTES:
        problems.append(t("the whole skill weighs {size} bytes (limit {limit})", size=total, limit=MAX_SKILL_BYTES))
    return problems


def _link_problems(root: Path, files: list[Path]) -> list[str]:
    """Links, which are refused: the copy would resolve them to bytes from elsewhere."""
    for path in files:
        if path.is_symlink():
            return [t("{name} is a link: the copy would hold bytes that are not in the skill",
                      name=path.relative_to(root).as_posix())]
    return []


def _shape_problems(root: Path, files: list[Path]) -> list[str]:
    """What is not inert text: files that can run, hooks, unknown bytes."""
    review: list[str] = []
    for path in files:
        name = path.relative_to(root).as_posix()
        if (
            path.name in HOOK_NAMES
            or path.suffix.lower() in SCRIPT_SUFFIXES
            or _is_executable(path)
            or path.suffix.lower() not in INERT_SUFFIXES
        ):
            review.append(name)
    if review:
        return [t("holds files a person should read first: {files}", files=", ".join(sorted(set(review))[:8]))]
    return []


def _secret_problems(files: list[Path]) -> list[str]:
    """Credentials sitting in the text: the one thing that always blocks."""
    hits: list[str] = []
    for path in files:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # nexgen: allow reason="a file that cannot be read is held by the shape check, not hidden here"
            continue
        if secret_shapes.looks_like_secret_value(text):
            hits.append(path.name)
    if hits:
        return [t("carries what looks like a credential in {files}: move it into a secret reference first",
                  files=", ".join(sorted(set(hits))[:5]))]
    return []


def _frontmatter_problems(root: Path) -> tuple[list[str], list[str]]:
    """(blocking, review) problems of the frontmatter OpenClaw's own loader requires."""
    body = root / "SKILL.md"
    data = frontmatter(body)
    if data is None:
        return [t("SKILL.md has no readable frontmatter: OpenClaw needs a name and a description")], []
    name = str(data.get("name") or "").strip()
    description = str(data.get("description") or "").strip()
    blocking: list[str] = []
    if not name:
        blocking.append(t("the frontmatter declares no name"))
    if not description:
        blocking.append(t("the frontmatter declares no description"))
    elif len(description.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
        blocking.append(t("the description is longer than {limit} bytes", limit=MAX_DESCRIPTION_BYTES))
    review = [t("its folder is '{folder}' but its frontmatter says '{name}'", folder=root.name, name=name)] \
        if name and name != root.name else []
    return blocking, review


def vet_skill(skill: WorkshopSkill) -> Verdict:
    """Whether this learned skill may enter the library, and what a person must see first."""
    root = Path(skill.path)
    if not is_safe_skill_name(skill.name):
        return Verdict(BLOCKED, (t("'{name}' cannot become a folder name", name=skill.name),))
    if root.is_symlink() or not root.is_dir():
        return Verdict(BLOCKED, (t("the skill folder is a link or is gone: nothing to copy"),))
    if not (root / "SKILL.md").is_file() or (root / "SKILL.md").is_symlink():
        return Verdict(BLOCKED, (t("no SKILL.md in the folder: OpenClaw would not load it either"),))
    files = _files(root)
    blocking = _secret_problems(files)
    blocking.extend(_link_problems(root, files))
    blocking.extend(_size_problems(root, files))
    front_blocking, front_review = _frontmatter_problems(root)
    blocking.extend(front_blocking)
    if blocking:
        return Verdict(BLOCKED, tuple(blocking))
    review = _shape_problems(root, files) + front_review
    return Verdict(REVIEW, tuple(review)) if review else Verdict(SAFE)


def _secret_in_server(spec: Mapping[str, Any]) -> list[str]:
    """Literal credentials in an MCP entry: env values, args, headers, URL userinfo."""
    hits: list[str] = []
    env = spec.get("env") if isinstance(spec.get("env"), Mapping) else {}
    for key, value in env.items():
        if not isinstance(value, str) or (value.startswith("${") and value.endswith("}")):
            continue
        if secret_shapes.looks_like_secret_value(value) or secret_shapes.SENSITIVE_NAME_SUBSTRING.search(str(key)):
            hits.append(str(key))
    for arg in spec.get("args") or []:
        if isinstance(arg, str) and secret_shapes.looks_like_secret_value(arg):
            hits.append("args")
    headers = spec.get("headers") if isinstance(spec.get("headers"), Mapping) else {}
    for key, value in headers.items():
        if isinstance(value, str) and secret_shapes.looks_like_secret_value(value):
            hits.append(str(key))
    url = spec.get("url")
    if isinstance(url, str) and secret_shapes.redact_url(url) != url:
        hits.append("url")
    return sorted(set(hits))


def _server_shape_problems(spec: Mapping[str, Any]) -> list[str]:
    """What would produce an entry the renderer cannot honor anyway."""
    problems: list[str] = []
    url = spec.get("url")
    command = spec.get("command")
    if url is not None:
        parsed = urlsplit(str(url))
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            problems.append(t("its URL is not http(s)"))
    elif not isinstance(command, str) or not command.strip():
        problems.append(t("it has neither a URL nor a command"))
    elif "\n" in str(command) or any("\n" in str(arg) for arg in spec.get("args") or []):
        problems.append(t("its command carries a newline"))
    return problems


def vet_server(server: UnmanagedServer) -> Verdict:
    """Whether this live MCP entry may enter the manifest, and why not when it may not."""
    if not SERVER_NAME_RE.fullmatch(server.name):
        return Verdict(BLOCKED, (t("'{name}' is not a safe manifest key", name=server.name),))
    secrets_found = _secret_in_server(server.spec)
    blocking = [
        t("{keys}: this entry carries a credential in plain text. Store it with `nexgen-secrets set` "
          "and reference it as ${{VARIABLE}} instead",
          keys=", ".join(secrets_found))
    ] if secrets_found else []
    blocking.extend(_server_shape_problems(server.spec))
    return Verdict(BLOCKED, tuple(blocking)) if blocking else Verdict(SAFE)

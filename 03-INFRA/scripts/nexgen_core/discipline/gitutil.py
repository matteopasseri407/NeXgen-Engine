"""Thin git layer: which files changed between a base and a head, with old and new text."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

STAGED = ":"  # head value meaning "the index"
IGNORED = ":(exclude).nexgen-discipline"  # review artifacts must not alter the reviewed diff


class GitError(RuntimeError):
    pass


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(root), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout if proc.returncode == 0 else ""


def repo_root(cwd: Path) -> Path:
    return Path(git(cwd, "rev-parse", "--show-toplevel").strip())


def resolve_base(root: Path, base: str | None = None) -> str:
    """Explicit base, else the fork point from upstream or the default branch, else the empty tree."""
    if base:
        return base
    for candidate in ("@{upstream}", "origin/HEAD", "origin/main", "origin/master",
                      "origin/developer", "main", "master"):
        out = git(root, "merge-base", "HEAD", candidate, check=False).strip()
        if out:
            return out
    return git(root, "hash-object", "-t", "tree", "/dev/null").strip()


@dataclass
class Change:
    path: str
    status: str  # A, M or D
    old: str | None
    new: str | None
    old_path: str | None = None  # the previous name when the file was moved


def _diff_args(base: str, head: str | None) -> list[str]:
    args = ["--cached"] if head == STAGED else []
    args.append(base)
    if head not in (None, STAGED):
        args.append(head)
    return args


def show(root: Path, spec: str) -> str | None:
    proc = subprocess.run(["git", "-C", str(root), "show", spec], capture_output=True)
    return proc.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n") if proc.returncode == 0 else None


def read_new(root: Path, path: str, head: str | None) -> str | None:
    if head is None:
        file = root / path
        return file.read_text(encoding="utf-8", errors="replace") if file.is_file() else None
    return show(root, f":{path}" if head == STAGED else f"{head}:{path}")


def changes(root: Path, base: str, head: str | None = None, renames: bool = True) -> list[Change]:
    """Files that differ from `base`. head=None is the working tree, STAGED the index."""
    out = git(root, "diff", "--name-status", "-M" if renames else "--no-renames", *_diff_args(base, head), "--", ".", IGNORED)
    entries = [line.split("\t") for line in out.splitlines() if "\t" in line]
    if head is None:
        untracked = git(root, "ls-files", "--others", "--exclude-standard", "--", ".", IGNORED)
        entries += [["A", p] for p in untracked.splitlines()]
    result = []
    for parts in entries:
        status, path = parts[0][0], parts[-1]
        old_path = parts[1] if status == "R" else path  # a moved file is compared with its old self
        status = "M" if status == "R" else status
        old = None if status == "A" else show(root, f"{base}:{old_path}")
        new = None if status == "D" else read_new(root, path, head)
        result.append(Change(path, status, old, new, old_path if old_path != path else None))
    return result


PUBLISHED = ("origin/main", "main")


def parents(root: Path, rev: str) -> list[str]:
    return git(root, "rev-list", "--parents", "-n", "1", rev, check=False).split()[1:]


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    proc = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
                          capture_output=True)
    return proc.returncode == 0


def own_changes(root: Path, base: str, head: str | None, renames: bool = True) -> list[Change]:
    """Changes made by the branch itself: a merge of published main does not hide or add authored work.

    Files whose content is byte-identical to the same path in a published merged-in parent came
    from main and are dropped; anything else in the merge commit (conflict fixes, extra code) stays."""
    found = changes(root, base, head, renames)
    if head in (None, STAGED):
        return found
    merged_in = parents(root, head)[1:]
    if not merged_in or not all(any(is_ancestor(root, p, ref) for ref in PUBLISHED) for p in merged_in):
        return found
    return [c for c in found if all(show(root, f"{p}:{c.path}") != c.new for p in merged_in)]


def fork_point(root: Path, ref: str, rev: str = "HEAD") -> str | None:
    return git(root, "merge-base", rev, ref, check=False).strip() or None


def diff_text(root: Path, base: str, head: str | None = None) -> str:
    text = git(root, "diff", "--no-renames", *_diff_args(base, head), "--", ".", IGNORED)
    if head is None:
        for path in git(root, "ls-files", "--others", "--exclude-standard", "--", ".", IGNORED).splitlines():
            proc = subprocess.run(["git", "-C", str(root), "diff", "--no-index", "--", "/dev/null", path],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace")
            text += proc.stdout
    return text


def files_at(root: Path, rev: str, suffixes: tuple[str, ...]) -> dict[str, str]:
    """Text of every file at `rev` with one of the suffixes, read with a single git process."""
    names = [n for n in git(root, "ls-tree", "-r", "--name-only", rev, check=False).splitlines()
             if n.lower().endswith(suffixes)]
    if not names:
        return {}
    proc = subprocess.run(["git", "-C", str(root), "cat-file", "--batch"], input="".join(f"{rev}:{n}\n" for n in names).encode(),
                          capture_output=True)
    out, pos, texts = proc.stdout, 0, {}
    for name in names:
        end = out.find(b"\n", pos)
        if end < 0:
            break
        header = out[pos:end].split()
        pos = end + 1
        if len(header) == 3 and header[2].isdigit():  # "<sha> <type> <size>"; "<rev:path> missing" has no body
            size = int(header[2])
            if header[1] == b"blob":
                texts[name] = out[pos:pos + size].decode("utf-8", errors="replace")
            pos += size + 1
    return texts

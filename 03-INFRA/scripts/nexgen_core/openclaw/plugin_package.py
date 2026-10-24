"""The `nexgen` plugin as a package: the same public files, in an npm tarball.

The deposit copies the plugin into OpenClaw's `extensions/` folder, which works only on a
gateway whose filesystem the engine can write. A gateway inside another machine's distro --
the one the Windows app ships -- has no `C:` and a filesystem the engine must not touch, and
OpenClaw installs plugins there from an npm tarball (`openclaw plugins install
npm-pack:<file.tgz>`). This module builds that tarball from the list the deposit itself
reads, so the two destinations can never disagree about what the plugin is.

Two properties are the point:

- the bytes are reproducible: the same files produce the same archive and the same sha256,
  because an artifact whose hash changes on every build cannot be the thing an install is
  verified against;
- nothing private rides along: the guardrail body and the engine's switches are written by
  the deposit from the Vault, never copied out of the plugin folder, so the package carries
  the mechanism and the policy stays on the machine that owns it.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path

from nexgen_core import __version__
from nexgen_core.errors import NexgenError
from nexgen_core.files import atomic_write_bytes
from nexgen_core.paths import resolve_engine_root, resolve_state_dir
from nexgen_core.runtimes.openclaw import PLUGIN_ID, plugin_files

#: Where the artifact lands inside the engine's state folder, beside the rest of the
#: machine-local state the engine keeps for OpenClaw. One file per version, named for it.
PACKAGE_SUBDIR = ("openclaw", "plugin")
#: npm's own layout: every file under a single `package/` root, which is what an installer
#: strips before copying the plugin into place.
ARCHIVE_ROOT = "package"
#: Fixed dates and permissions: the source files' own mtimes and modes must not reach the
#: archive, or two builds of the same content would not be the same artifact.
_FIXED_MTIME = 0
_FIXED_MODE = 0o644
_PACKAGE_JSON = "package.json"


@dataclass(frozen=True)
class PackageResult:
    """What the builder produced: where it is, its hash, and the files inside it."""

    path: Path
    sha256: str
    files: tuple[str, ...]


def build_package(
    home: Path | None = None,
    engine_root: Path | None = None,
    engine_hooks_dir: Path | None = None,
    output_dir: Path | None = None,
    version: str | None = None,
) -> PackageResult:
    """Builds the installable tarball and returns its path and hash; it installs nothing.

    `engine_hooks_dir` and `output_dir` are the seams a test needs; the command passes none
    of them and gets the engine's own hooks folder and state folder.
    """
    hooks_dir = engine_hooks_dir or resolve_engine_root(home, override=engine_root) / "agent-universal-layer" / "hooks"
    files = plugin_files(hooks_dir)  # raises when the plugin is incomplete
    engine_version = version or __version__
    if not engine_version or engine_version == "unknown":
        raise NexgenError("openclaw: the engine version is unreadable, so the package cannot be named")

    entries = {name: source.read_bytes() for name, source in files.items()}
    source_manifest = entries.get(_PACKAGE_JSON)
    if source_manifest is None:
        raise NexgenError(f"openclaw: the plugin has no {_PACKAGE_JSON} ({hooks_dir})")
    entries[_PACKAGE_JSON] = _artifact_package_json(source_manifest, engine_version)
    payload = _tarball(entries)

    target_dir = Path(output_dir) if output_dir is not None else resolve_state_dir(home).joinpath(*PACKAGE_SUBDIR)
    target = target_dir / f"{PLUGIN_ID}-{engine_version}.tgz"
    atomic_write_bytes(target, payload)
    return PackageResult(path=target, sha256=hashlib.sha256(payload).hexdigest(), files=tuple(sorted(entries)))


def _artifact_package_json(source: bytes, version: str) -> bytes:
    """The plugin's own `package.json`, carrying what OpenClaw reads before it loads any code.

    `openclaw.extensions` must be declared and point inside the package (OpenClaw refuses an
    install without it), `name` is the plugin id the folder and the manifest already agree on,
    and `version` is the engine's: the artifact ships with the engine, not on its own clock.
    Everything else the plugin declares -- `private` above all, since this package is built to
    be installed locally and never published -- travels unchanged.
    """
    try:
        data = json.loads(source.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as err:
        raise NexgenError(f"openclaw: the plugin's {_PACKAGE_JSON} is not readable JSON: {err}") from err
    if not isinstance(data, dict):
        raise NexgenError(f"openclaw: the plugin's {_PACKAGE_JSON} is not an object")
    metadata = data.get("openclaw")
    if not isinstance(metadata, dict) or not metadata.get("extensions"):
        raise NexgenError("openclaw: the plugin's package.json declares no openclaw.extensions entry")
    data["name"] = PLUGIN_ID
    data["version"] = version
    data["type"] = "module"
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _tarball(entries: dict[str, bytes]) -> bytes:
    """An npm-shaped `.tgz`: `package/` root, sorted members, one date, one permission."""
    buffer = io.BytesIO()
    # `filename` empty and `mtime` zero: gzip otherwise records the output file's name and
    # the wall clock in its header, and the hash would change on every build.
    with (
        gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=_FIXED_MTIME) as compressed,
        # `encoding` is how the archive names its members: UTF-8, named rather than left to
        # the default, because a plugin file with an accented name must land the same on
        # every machine that installs the tarball.
        tarfile.open(mode="w", fileobj=compressed, format=tarfile.PAX_FORMAT, encoding="utf-8") as archive,
    ):
        for name in sorted(entries):
            body = entries[name]
            info = tarfile.TarInfo(f"{ARCHIVE_ROOT}/{name}")
            info.size = len(body)
            info.mtime = _FIXED_MTIME
            info.mode = _FIXED_MODE
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.type = tarfile.REGTYPE
            archive.addfile(info, io.BytesIO(body))
    return buffer.getvalue()

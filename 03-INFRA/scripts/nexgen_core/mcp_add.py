"""`nexgen mcp add`: the one-command app install for MCP servers.

Before this, adding a connector meant opening `mcp/manifest.yaml` by hand
and writing YAML with exactly the right indentation, in a file whose
mistakes surface as a failed sync on the next guard cycle — four CLIs
blocked by a two-space slip. The manifest stays the single canonical
source; what changes is who writes the YAML: this command does, atomically,
with a backup and re-validation, so a bad add ends exactly where it started.

The write path is shared with `--adopt` (one implementation of "append
under `servers:` with backup + validation + rollback"), and the same
semantic validation that guards imported stubs guards hand-declared ones:
a server name that means something else in YAML, a URL whose scheme is not
http(s), a command carrying newlines are refused before anything is written.

Secrets never land in the manifest in clear: `--auth-env` takes a variable
NAME, and an `--env` value shaped like a credential is refused with the
`${VAR}` form it must use instead. The renderer expands placeholders from
the environment at render time; the manifest only ever carries references.
"""
from __future__ import annotations

import re

from nexgen_core.config import RUNTIME_TARGETS, load_mcp_manifest
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_home, resolve_vault_data

#: Same shape the renderer accepts as a section name elsewhere; a name that
#: escapes this is exactly the shape that means something else in YAML.
SERVER_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

#: Values that are credentials wearing plain strings. The manifest must
#: carry `${VAR}` references, never these.
SECRET_VALUE_RE = re.compile(
    r"(\b(sk-[A-Za-z0-9_-]{16,})"          # OpenAI-style keys
    r"|(\bghp_[A-Za-z0-9]{20,})"           # GitHub PATs
    r"|(\bAKIA[0-9A-Z]{12,})"              # AWS access keys
    r"|(\b[A-Fa-f0-9]{40,}\b)"             # long hex runs
    r"|(\b[A-Za-z0-9+/_-]{43,}={0,2}\b))"  # long base64-ish runs
)


def _manifest_path(vault_data) -> object:
    return vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"


def parse_targets(raw: str | None) -> list[str]:
    """`--targets codex,claude` or `--targets all`, validated against the
    same RUNTIME_TARGETS the renderer honors."""
    if raw is None or raw.strip() == "" or raw.strip().lower() == "all":
        return sorted(RUNTIME_TARGETS)
    targets = [t.strip() for t in raw.split(",") if t.strip()]
    unknown = [t for t in targets if t not in RUNTIME_TARGETS]
    if unknown:
        raise ValueError(t(
            "unknown target(s): {unknown} (known: {known})",
            unknown=", ".join(unknown), known=", ".join(sorted(RUNTIME_TARGETS)),
        ))
    if not targets:
        raise ValueError(t("--targets selected no runtime"))
    return targets


def _parse_env(pairs: list[str] | None) -> dict[str, str]:
    env: dict[str, str] = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise ValueError(t("--env entries must be KEY=VALUE, got {pair}", pair=pair))
        value = value.strip()
        if SECRET_VALUE_RE.search(value) and not value.startswith("${"):
            raise ValueError(t(
                "the value of {key} looks like a credential: pass an environment "
                "reference instead, {key}=${{SOME_VAR}}",
                key=key.strip(),
            ))
        env[key.strip()] = value
    return env


def build_entry(
    *,
    name: str,
    targets: list[str],
    command: str | None,
    args: list[str] | None,
    url: str | None,
    auth_env: str | None,
    env: dict[str, str] | None,
    lazy: bool,
    readonly: bool,
) -> dict:
    """The manifest entry, validated before anything is written."""
    from nexgen_core.renderer_cli import _validate_stub_entry

    if not SERVER_NAME_RE.fullmatch(name or ""):
        raise ValueError(t(
            "'{name}' is not a safe manifest key (letters, digits, dot, dash, underscore)",
            name=name,
        ))
    if bool(command) == bool(url):
        raise ValueError(t("give exactly one of --command/--url"))
    if command and not isinstance(command, str):
        raise ValueError(t("--command must be a plain string"))
    if url and not url.startswith(("http://", "https://")):
        raise ValueError(t("--url must be an http(s) URL"))
    if readonly and not lazy:
        raise ValueError(t(
            "--readonly only means something behind the lazy proxy; add --lazy",
        ))

    entry: dict = {}
    # Mounted by default: an `add` that silently produced an inert entry
    # (the tier gate's default) would be a command that reports success and
    # mounts nothing. Opting OUT of mounting is explicit: --lazy.
    entry["tier"] = "core"
    if url:
        entry["transport"] = "http"
        entry["url"] = url
        if auth_env:
            entry["auth"] = {"env": auth_env}
    else:
        entry["transport"] = "stdio"
        entry["command"] = command
        if args:
            entry["args"] = list(args)
        if env:
            entry["env"] = dict(env)
    if lazy:
        entry["lazy"] = True
        if readonly:
            entry["readonly"] = True
    entry["targets"] = list(targets)

    problem = _validate_stub_entry(name, entry)
    if problem:
        raise ValueError(t("refused: {reason}", reason=problem))
    return entry


def add_server(
    name: str,
    targets_raw: str | None,
    *,
    command: str | None = None,
    args: list[str] | None = None,
    url: str | None = None,
    auth_env: str | None = None,
    env_pairs: list[str] | None = None,
    lazy: bool = False,
    readonly: bool = False,
    dry_run: bool = False,
    home=None,
    vault_data=None,
) -> tuple[int, str]:
    """Adds one server to the manifest: validated, atomic, roll-backed.

    Returns (exit code, message). Nothing is rendered here on purpose: the
    manifest is the canonical source, and the next `nexgen sync apply` (or
    the guard cycle) regenerates the CLIs from it — the same rule that keeps
    hand edits and adopted stubs honest.
    """
    from nexgen_core.renderer_cli import _emit_manifest_stub, insert_server_stubs

    try:
        targets = parse_targets(targets_raw)
        env = _parse_env(env_pairs)
        entry = build_entry(
            name=name, targets=targets, command=command, args=args, url=url,
            auth_env=auth_env, env=env, lazy=lazy, readonly=readonly,
        )
    except ValueError as exc:
        return 2, str(exc)

    home_dir = resolve_home(home)
    vault = resolve_vault_data(home_dir, vault_data)
    path = _manifest_path(vault)

    try:
        data = load_mcp_manifest(path)  # type: ignore[arg-type]
    except Exception as exc:
        return 2, t("cannot read the manifest ({error}); fix it before adding", error=exc)

    if name in (data.get("servers") or {}):
        return 2, t("'{name}' is already declared in the manifest: edit it by hand.", name=name)
    if name in (data.get("retired_servers") or []):
        return 2, t(
            "'{name}' is retired: remove it from retired_servers first if you really mean to bring it back.",
            name=name,
        )

    stub = _emit_manifest_stub(name, entry)
    if dry_run:
        return 0, t("dry run (nothing written). Stub to add under 'servers:':\n{stub}", stub=stub)

    ok, message, _backup = insert_server_stubs(path, [stub])  # type: ignore[arg-type]
    if not ok:
        return 2, message
    return 0, t(
        "{name} added to the manifest for {targets}. Backup: {backup}. "
        "Run 'nexgen sync apply' to render it to the CLIs, then commit.",
        name=name, targets=", ".join(targets), backup=_backup,
    )

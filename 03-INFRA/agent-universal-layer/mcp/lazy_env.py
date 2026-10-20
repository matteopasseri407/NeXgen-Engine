#!/usr/bin/env python3
"""lazy_env.py — what a lazy server's child process is allowed to see.

The gateway starts servers it did not write, so its environment policy lives in one place:
the credential chain (the process, then the machine's session, then the secrets deposit),
the `${NAME}` expansion of what a manifest entry declares, the filter of variables a child
may inherit, and the redaction of the values the gateway handed over. Nothing here decides
which server runs or when it starts; the caller passes the entry it is working on.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Callable

#: What a child server may inherit. The proxy's own environment holds every token the user's shell
#: exports; a server that needs one declares it (`env: {NAME: "${NAME}"}` in the manifest entry) instead
#: of getting all of them. The list is what runtimes need to start at all (`npx`, `node`, python,
#: docker, a proxy) plus the engine's own variables; the MCP SDK's default is stricter still.
INHERITED_EXACT = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TMP", "TEMP",
    "PWD", "DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "VIRTUAL_ENV", "PYTHONPATH",
    "PYTHONIOENCODING", "PYTHONUTF8", "NODE_PATH", "NODE_OPTIONS", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE",
    "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "NO_PROXY", "DOCKER_HOST", "COLORTERM", "LD_LIBRARY_PATH",
    # Windows
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "USERNAME", "USERDOMAIN",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "COMMONPROGRAMFILES", "HOMEDRIVE", "HOMEPATH", "ALLUSERSPROFILE", "PUBLIC", "COMPUTERNAME", "OS",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
})
INHERITED_PREFIXES = (
    "LC_", "XDG_", "NEXGEN_", "AGENT_", "KNOWLEDGE_VAULT_", "NPM_CONFIG_", "NODE_", "NVM_", "VOLTA_", "UV_",
    "PIPX_", "PYTHON", "LAZY_MCP_",
)


def machine_environment(config_home: str, cache: tuple[tuple, dict[str, str]]) -> tuple[tuple, dict[str, str]]:
    """The variables this user's machine declares for every session: `~/.config/environment.d/*.conf`.

    A CLI inherits them only if it was started from the graphical session; started over SSH, from a service or
    from some other launcher it does not, and then a server that needs one of its tokens silently went missing
    ("Vercel is not there"). The gateway resolves the variables a manifest entry *declares* from here when the
    process does not have them, so what a server can reach no longer depends on how the CLI happened to be
    launched. POSIX only: Windows keeps user variables in the registry, which every process inherits.

    The cache is the caller's, handed back with the values: a loaded gateway keeps its own, so a reset done on
    that module is a real reset. `config_home` is where `environment.d` lives (XDG or the user's `.config`).
    """
    if os.name == "nt":
        return cache, {}
    directory = Path(config_home, "environment.d")
    files = sorted(directory.glob("*.conf")) if directory.is_dir() else []
    try:
        signature = tuple((str(path), path.stat().st_mtime_ns) for path in files)
    except OSError:
        return cache, {}
    if signature == cache[0]:
        return cache, cache[1]
    values: dict[str, str] = {}
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:  # nexgen: allow reason="one unreadable environment.d file must not hide the others"
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    return (signature, values), values


def env_default(name: str, default: str, machine: dict[str, str]) -> str:
    """A variable from the process, then from the machine's session; the default only when neither has it."""
    value = os.environ.get(name)
    if value is None:
        value = machine.get(name)
    return default if value is None else value


def deposit_env():
    """The shared reader of the secrets deposit's materialized file (see nexgen_core.deposit_env)."""
    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from nexgen_core import deposit_env as _deposit_env

    return _deposit_env


def declared_value(name: str, machine: dict[str, str]) -> str:
    """A credential a manifest entry declares it needs: the process, then the machine's session, then the deposit."""
    value = env_default(name, "", machine)
    return value or deposit_env().read_deposit().get(name, "")


def expand_placeholders(text: str, ctx: dict[str, str], env_default: Callable[[str, str], str]) -> str:
    """`${NAME}` and `${NAME:-fallback}`: the context first, then the session lookup the caller supplies."""
    def repl(match):
        name, default = match.group(1), ""
        if ":-" in name:
            name, default = name.split(":-", 1)
        return ctx.get(name, env_default(name, default))
    return re.sub(r"\$\{([^}]+)\}", repl, text)


class Credentials:
    """The credential chain of one gateway load, asked afresh on every use.

    It holds the lookup that reads the machine's session, never a snapshot of it: a file that changes while the
    gateway runs is picked up by the caller's cache, exactly as it was before the split.
    """

    def __init__(self, machine: Callable[[], dict[str, str]]) -> None:
        self._machine = machine

    def default(self, name: str, fallback: str = "") -> str:
        """A variable this session can see, from the process or from the machine's session."""
        return env_default(name, fallback, self._machine())

    def declared(self, name: str) -> str:
        """A credential a manifest entry declares it needs, looked up everywhere one can live."""
        return declared_value(name, self._machine())

    def expand(self, text: str, ctx: dict[str, str]) -> str:
        """The entry's `${NAME}` placeholders, resolved against this session."""
        return expand_placeholders(text, ctx, self.default)


def secret_shapes():
    """The shared definition of what a secret looks like (see nexgen_core.secret_shapes)."""
    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from nexgen_core import secret_shapes as _secret_shapes

    return _secret_shapes


def is_secret_name(name: str) -> bool:
    """A name that looks like a secret is never inherited, even under an allowed prefix."""
    return secret_shapes().is_secret_name(name)


def child_environment(declared: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a child server starts with: the allowed part of the proxy's own, then what the
    manifest entry declared (declared means wanted, so a secret named there is passed on purpose)."""
    inherited = {
        name: value for name, value in os.environ.items()
        if (name.upper() in INHERITED_EXACT or name.upper().startswith(INHERITED_PREFIXES))
        and not is_secret_name(name)
    }
    inherited.update(declared or {})
    return inherited


def redact(text: str, env: dict[str, str]) -> str:
    """Hides, in text a child printed, the values of the secrets this proxy handed it, and the usual shapes."""
    for name, value in env.items():
        if is_secret_name(name) and len(value) >= 6:
            text = text.replace(value, "[redacted]")
    return secret_shapes().redact(text)

# nexgen: one-job reason="take one snapshot of what this machine already has, so every command decides from the same facts"
"""What this machine already has, asked in the least invasive way possible.

Every credential is only asked whether it EXISTS, with one deliberate
exception: OpenCode's `auth.json`, which is read for its top-level key names
and nothing else, to learn whether an `opencode-go` login is there. No value is
ever returned, logged or formatted. Everything else under a home is only
tested for existence, apart from a package's own `package.json` and the
configuration OpenClaw already wrote.

The two model backends are probed with JSON against a loopback URL through a
client the caller injects, so a test answers without opening a socket and a
machine without Ollama costs a handful of short timeouts.

The result is the input of a pure function (`profile.build_config`), which is
why everything is gathered here once, in one place, and never re-read there.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.companion import Companion, probe as probe_companion
from nexgen_core.openclaw.package_resolve import resolve_openclaw_package
from nexgen_core.paths import openclaw_config, resolve_engine_root, resolve_home, resolve_vault_data

#: Ollama's own default, and the variable that overrides it (Ollama reads it too).
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
#: llama.cpp's server default, and the engine's own variable for it.
DEFAULT_LLAMACPP_URL = "http://127.0.0.1:8080"

#: A probe answers fast or not at all: a stopped backend must never delay a command.
PROBE_TIMEOUT_SECONDS = 1.5
#: How many local model calls are asked at the same time. Each one is a wait on
#: a loopback server, so the sum was the catalogue's cost on a machine with
#: several models.
PROBE_WORKERS = 4
#: `npm root -g` is a local read, not a network call.
NPM_ROOT_TIMEOUT_SECONDS = 20.0

#: A JSON client: `(url, timeout)` for a GET, `(url, timeout, payload)` for a POST.
HttpJson = Callable[..., Any]
Which = Callable[[str], "str | None"]
Run = Callable[..., Any]


class _Missing:
    """A marker for "this parameter was not given", distinct from a payload of None."""


_MISSING = _Missing()


@dataclass(frozen=True)
class Installation:
    """OpenClaw itself: its package, its version, and the file that configures it."""

    installed: bool
    version: str | None = None
    config_path: str = ""
    config_present: bool = False
    detail: str = ""
    node_requirement: str | None = None


@dataclass(frozen=True)
class Login:
    """A CLI the person may already pay for: its binary, and whether a login shows.

    `login` is ``None`` when the platform keeps the credential somewhere the
    engine refuses to look (macOS Keychain): the honest answer is "not
    verifiable", and the binary is taken as the sign of an install.
    """

    binary: bool
    login: bool | None = None
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.binary and self.login is not False


@dataclass(frozen=True)
class LocalModelInfo:
    """One model a local backend serves, with the facts OpenClaw needs about it.

    A provider block with only an `id` loses the context window, and OpenClaw
    then miscounts and overflows. `context_window` is None when the backend did
    not tell us, and the plan says so instead of inventing a number.
    """

    name: str
    context_window: int | None = None
    vision: bool = False
    reasoning: bool = False


@dataclass(frozen=True)
class LocalModels:
    """The models this machine serves itself, by provider, exactly as reported."""

    ollama_url: str = DEFAULT_OLLAMA_URL
    ollama: tuple[LocalModelInfo, ...] = ()
    llamacpp_url: str = DEFAULT_LLAMACPP_URL
    llamacpp: tuple[LocalModelInfo, ...] = ()
    detail: str = ""

    @property
    def available(self) -> bool:
        return bool(self.ollama or self.llamacpp)

    @property
    def ollama_names(self) -> tuple[str, ...]:
        return tuple(model.name for model in self.ollama)

    @property
    def llamacpp_names(self) -> tuple[str, ...]:
        return tuple(model.name for model in self.llamacpp)


@dataclass(frozen=True)
class Detection:
    """The whole answer: one snapshot, taken once, of what is on this machine."""

    openclaw: Installation
    claude: Login
    codex: Login
    opencode_go: bool
    local: LocalModels
    n8n: bool
    #: OpenCode's own command on PATH: the harness the ACP agent launches.
    opencode: bool = False
    #: The executable to hand the ACP bridge, resolved once (empty when unknown).
    opencode_command: str = ""
    #: The Windows app's own gateway: where it is, or that this machine has none.
    companion: Companion = Companion()

    def to_dict(self) -> dict[str, Any]:
        """The snapshot as JSON-safe data, for `plan --json` and the doctor."""
        return {
            "openclaw": asdict(self.openclaw),
            "claude": {**asdict(self.claude), "available": self.claude.available},
            "codex": {**asdict(self.codex), "available": self.codex.available},
            "opencode_go": self.opencode_go,
            "opencode": self.opencode,
            "opencode_command": self.opencode_command,
            "companion": self.companion.to_dict(),
            "local": {**asdict(self.local), "available": self.local.available},
            "n8n": self.n8n,
        }


def _http_json(url: str, timeout: float, data: Any = _MISSING) -> Any:
    """One JSON call against a local backend, GET unless a payload is given.

    A backend that is not running is a normal answer to a capability probe, not
    an error: the caller gets None and says "not reachable" in words.
    """
    try:
        if data is _MISSING:
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
        else:
            body = json.dumps(data).encode("utf-8")
            request = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback URL from configuration
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, ValueError):  # nexgen: allow reason="a stopped local backend is an answer to a probe, not a failure"
        return None


def _read_json(path: Path) -> Any:
    """A JSON file, or None when it is absent or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable JSON file means 'nothing there', and the doctor names the path"
        return None


def _npm_root(npm: str, run: Run, env: dict[str, str]) -> Path | None:
    """Where npm keeps its global packages, asked once and injectable in tests.

    The command is the resolved path, not the bare name: on Windows npm is
    `npm.cmd`, and a `.cmd` without a path is not executable by CreateProcess,
    which turned "OpenClaw is installed" into "not installed" on the machine
    this was written for.
    """
    try:
        completed = run(
            [npm, "root", "-g"], capture_output=True, text=True, check=False,
            timeout=NPM_ROOT_TIMEOUT_SECONDS, env=env, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):  # nexgen: allow reason="npm missing or wedged means 'cannot locate the package', which detection reports"
        return None
    lines = [line.strip() for line in str(getattr(completed, "stdout", "") or "").splitlines() if line.strip()]
    return Path(lines[-1]) if lines else None


def _installation_from_manifest(manifest: dict[str, Any], config: Path) -> Installation:
    """An installed OpenClaw record read from its parsed `package.json`."""
    engines = manifest.get("engines") if isinstance(manifest.get("engines"), dict) else {}
    requirement = engines.get("node")
    return Installation(
        installed=True,
        version=str(manifest["version"]),
        config_path=str(config),
        config_present=config.is_file(),
        node_requirement=str(requirement) if isinstance(requirement, str) and requirement.strip() else None,
    )


def _detect_openclaw(home: Path, env: dict[str, str], run: Run | None, which: Which, config: Path) -> Installation:
    """The package, its version from npm's own tree, and the configuration file."""
    run = run or subprocess.run
    npm = which("npm")
    if npm is None:
        binary = which("openclaw")
        # Another package manager's shim may still be on PATH: claiming it is
        # absent would send the person to install what is already there.
        return Installation(
            installed=binary is not None, version=None, config_path=str(config), config_present=config.is_file(),
            detail=(
                t("npm is not on PATH, so the version of {binary} cannot be read", binary=binary)
                if binary is not None
                else t("npm is not on PATH, so a global OpenClaw cannot be found or installed")
            ),
        )

    binary = which("openclaw")
    pkg_dir = resolve_openclaw_package(binary) if binary else None
    if pkg_dir is not None:
        manifest = _read_json(pkg_dir / "package.json")
        if isinstance(manifest, dict) and manifest.get("version"):
            return _installation_from_manifest(manifest, config)

    root = _npm_root(npm, run, env)
    if root is None:
        return Installation(
            installed=False, config_path=str(config), config_present=config.is_file(),
            detail=t("npm did not say where its global packages live"),
        )
    package = root / "openclaw" / "package.json"
    manifest = _read_json(package) if package.is_file() else None
    if isinstance(manifest, dict) and manifest.get("version"):
        return _installation_from_manifest(manifest, config)
    return Installation(
        installed=False, config_path=str(config), config_present=config.is_file(),
        detail=t("no npm package named 'openclaw' under {root}", root=str(root)),
    )


def _credential_exists(path: Path) -> bool:
    """Whether a login file is there. It is never opened, not even to look inside."""
    return path.exists()


def _detect_claude(home: Path, which: Which, platform: str) -> Login:
    """Claude Code: the binary, and the login file when the platform keeps one."""
    binary = which("claude") is not None
    if not binary:
        return Login(binary=False, login=None, detail=t("the 'claude' command is not on PATH"))
    if _credential_exists(home / ".claude" / ".credentials.json"):
        return Login(binary=True, login=True)
    if platform.startswith("darwin"):
        return Login(
            binary=True, login=None,
            detail=t("on macOS the login lives in the Keychain: not verifiable from here"),
        )
    return Login(binary=True, login=False, detail=t("no ~/.claude/.credentials.json: Claude Code is not logged in"))


def _detect_codex(home: Path, env: dict[str, str], which: Which) -> Login:
    """Codex: the binary, and `auth.json` under `CODEX_HOME` when that is set."""
    binary = which("codex") is not None
    if not binary:
        return Login(binary=False, login=None, detail=t("the 'codex' command is not on PATH"))
    codex_home = Path(env["CODEX_HOME"]).expanduser() if env.get("CODEX_HOME") else home / ".codex"
    if _credential_exists(codex_home / "auth.json"):
        return Login(binary=True, login=True)
    return Login(binary=True, login=False, detail=t("no auth.json in {home}: Codex is not logged in", home=str(codex_home)))


def _go_profile_in_config(config: Any) -> bool:
    """Whether OpenClaw already holds an `opencode-go` auth profile.

    The file carries a reference, never the key itself, so reading it is safe.
    """
    auth = config.get("auth") if isinstance(config, dict) else None
    profiles = auth.get("profiles") if isinstance(auth, dict) else None
    if isinstance(profiles, dict):
        items = list(profiles.values())
    elif isinstance(profiles, list):
        items = profiles
    else:
        return False
    return any(
        isinstance(profile, dict) and str(profile.get("provider", "")).strip().lower() == "opencode-go"
        for profile in items
    )


def _go_login_in_opencode(env: dict[str, str], home: Path) -> bool:
    """Whether OpenCode itself is logged in for `opencode-go`.

    Only the top-level key names of `auth.json` are looked at. The values are
    secrets and are never copied, returned, logged or formatted anywhere.
    """
    data_home = env.get("XDG_DATA_HOME")
    base = Path(data_home).expanduser() if data_home else home / ".local" / "share"
    loaded = _read_json(base / "opencode" / "auth.json")
    return isinstance(loaded, dict) and "opencode-go" in loaded


def _detect_opencode_go(home: Path, env: dict[str, str], config: Path) -> bool:
    """The Go key is available through any of the three places it can live.

    The variable is the simplest; OpenClaw may already hold the profile as a
    reference; OpenCode may already be logged in and the key reused from there.
    Only the presence matters, never the value.
    """
    if env.get("OPENCODE_API_KEY"):
        return True
    if _go_profile_in_config(_read_json(config)):
        return True
    return _go_login_in_opencode(env, home)


def _base_url(raw: str | None, default: str) -> str:
    """A local backend URL, with the scheme Ollama's own variable usually omits."""
    value = (raw or default).strip().rstrip("/")
    return value if value.startswith(("http://", "https://")) else f"http://{value}"


def _as_int(value: Any) -> int | None:
    """A positive integer, or None: a broken field is no context, not a crash."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()) or None
    return None


def _ollama_context(show: dict[str, Any]) -> int | None:
    """The context window Ollama reports: `num_ctx` first, then `context_length`."""
    parameters = show.get("parameters")
    if isinstance(parameters, dict):
        found = _as_int(parameters.get("num_ctx"))
        if found:
            return found
    if isinstance(parameters, str):
        for line in parameters.splitlines():
            key, _, rest = line.strip().partition(" ")
            if key == "num_ctx":
                found = _as_int(rest)
                if found:
                    return found
    info = show.get("model_info") if isinstance(show.get("model_info"), dict) else {}
    for key, value in info.items():
        if key.endswith(".context_length"):
            found = _as_int(value)
            if found:
                return found
    return None


def _capabilities(show: dict[str, Any]) -> set[str]:
    """Ollama's own capability list, lowercased, when it reports one."""
    listed = show.get("capabilities")
    if not isinstance(listed, list):
        return set()
    return {str(item).strip().lower() for item in listed}


def _ollama_vision(show: dict[str, Any]) -> bool:
    """Vision is a capability first, and a truthy `model_info` flag after that."""
    if "vision" in _capabilities(show):
        return True
    info = show.get("model_info") if isinstance(show.get("model_info"), dict) else {}
    return any("vision" in str(key).lower() and bool(value) for key, value in info.items())


def _ollama_reasoning(show: dict[str, Any]) -> bool:
    """Ollama calls a reasoning model "thinking" in its capability list."""
    return "thinking" in _capabilities(show)


def _ollama_model(url: str, name: str, probe: HttpJson) -> LocalModelInfo:
    """One model, with the facts its own `/api/show` adds to the tags list."""
    show = probe(f"{url}/api/show", PROBE_TIMEOUT_SECONDS, {"model": name, "name": name})
    details = show if isinstance(show, dict) else {}
    return LocalModelInfo(
        name=name,
        context_window=_ollama_context(details),
        vision=_ollama_vision(details),
        reasoning=_ollama_reasoning(details),
    )


def _probe_ollama(url: str, probe: HttpJson) -> tuple[LocalModelInfo, ...]:
    """The models Ollama reports, each with the facts its `/api/show` adds.

    The detail calls are one round trip per installed model, so they are asked
    at the same time: a machine with eight models was paying eight waits for a
    catalogue that a health summary reads once. The order is the one Ollama
    reported, never the order the answers came back in.
    """
    answer = probe(f"{url}/api/tags", PROBE_TIMEOUT_SECONDS)
    models = answer.get("models") if isinstance(answer, dict) else None
    if not isinstance(models, list):
        return ()
    names = [str(item["name"]) for item in models if isinstance(item, dict) and item.get("name")]
    if not names:
        return ()
    with ThreadPoolExecutor(max_workers=min(PROBE_WORKERS, len(names))) as pool:
        return tuple(pool.map(lambda name: _ollama_model(url, name, probe), names))


def _llamacpp_context(url: str, probe: HttpJson) -> int | None:
    """llama.cpp's context window, from the one place it reports it."""
    answer = probe(f"{url}/props", PROBE_TIMEOUT_SECONDS)
    settings = answer.get("default_generation_settings") if isinstance(answer, dict) else None
    return _as_int(settings.get("n_ctx")) if isinstance(settings, dict) else None


def _probe_llamacpp(url: str, probe: HttpJson) -> tuple[LocalModelInfo, ...]:
    """The model ids llama.cpp's OpenAI-compatible endpoint reports."""
    answer = probe(f"{url}/v1/models", PROBE_TIMEOUT_SECONDS)
    data = answer.get("data") if isinstance(answer, dict) else None
    if not isinstance(data, list):
        return ()
    context = _llamacpp_context(url, probe)
    return tuple(
        LocalModelInfo(name=str(item["id"]), context_window=context)
        for item in data
        if isinstance(item, dict) and item.get("id")
    )


def _detect_local(env: dict[str, str], probe: HttpJson) -> LocalModels:
    """Both local backends, each with its own URL and its own short timeout.

    Asked at the same time: they are two independent servers on this machine and
    a health summary paid the sum of their waits. Each one is still bounded by
    its own timeout, so a wedged backend cannot hold the other.
    """
    ollama_url = _base_url(env.get("OLLAMA_HOST"), DEFAULT_OLLAMA_URL)
    llamacpp_url = _base_url(env.get("NEXGEN_LLAMACPP_URL"), DEFAULT_LLAMACPP_URL)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ollama_call = pool.submit(_probe_ollama, ollama_url, probe)
        llamacpp_call = pool.submit(_probe_llamacpp, llamacpp_url, probe)
        ollama, llamacpp = ollama_call.result(), llamacpp_call.result()
    detail = ""
    if not ollama and not llamacpp:
        detail = t("no local model backend answered")
    return LocalModels(ollama_url=ollama_url, ollama=ollama, llamacpp_url=llamacpp_url, llamacpp=llamacpp, detail=detail)


def _module_state(module_id: str, engine_root: Path, vault_data: Path, env: dict[str, str]) -> str:
    """What the engine says a module is here, through its own catalog API."""
    from nexgen_core.modules import derive_state, external_paths, load_catalog, load_state_file

    try:
        catalog = load_catalog(engine_root, external=external_paths(vault_data))
        states = derive_state(catalog, load_state_file(vault_data), env=env)
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable catalog means 'not active', and the catalog check reports it"
        return "absent"
    for item in states:
        if item.module.id == module_id:
            return item.state
    return "absent"


def detect(
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    *,
    which: Which | None = None,
    run: Run | None = None,
    http_json: HttpJson | None = None,
    engine_root: Path | None = None,
    vault_data: Path | None = None,
    platform: str | None = None,
    wsl_distributions: Callable[[], list[str]] | None = None,
) -> Detection:
    """What this machine has, in one snapshot.

    Every collaborator is injected so the detection itself can be tested with
    fake files and a fake HTTP client, and never touches the machine running
    the tests.
    """
    values = dict(os.environ) if env is None else {str(k): str(v) for k, v in env.items()}
    home_dir = resolve_home(home)
    resolved_which: Which = which or shutil.which
    probe: HttpJson = http_json or _http_json
    system = platform or sys.platform
    engine = Path(engine_root) if engine_root is not None else resolve_engine_root(home_dir)
    vault = Path(vault_data) if vault_data is not None else resolve_vault_data(home_dir)
    config = openclaw_config(home_dir, values)

    return Detection(
        openclaw=_detect_openclaw(home_dir, values, run, resolved_which, config),
        claude=_detect_claude(home_dir, resolved_which, system),
        codex=_detect_codex(home_dir, values, resolved_which),
        opencode_go=_detect_opencode_go(home_dir, values, config),
        local=_detect_local(values, probe),
        # The owner keeps n8n on a VPS: remote is active too, not only local.
        n8n=_module_state("n8n", engine, vault, values) in ("local", "remote"),
        # The command line itself, not a login: the ACP agent launches it.
        opencode=resolved_which("opencode") is not None,
        opencode_command=opencode_command(resolved_which("opencode")),
        # The Windows app may run the gateway of this machine instead of npm.
        companion=probe_companion(home_dir, values, system, wsl_distributions),
    )


def opencode_command(found: str | None) -> str:
    """The OpenCode executable to give the ACP bridge, or "" when there is none.

    The bridge's own default is `npx opencode-ai acp`, which fetches the old V1
    package: it refuses a V2 `opencode.json` and exits at once. The installed
    OpenCode speaks ACP itself (`opencode acp`). npm puts a `.cmd` shim on PATH
    on Windows; the real executable sits behind it, and an absolute executable
    path is the shape the bridge takes as one argument.
    """
    if not found:
        return ""
    shim = Path(found)
    if shim.suffix.lower() in (".cmd", ".ps1", "") and shim.parent.is_dir():
        for name in ("opencode.exe", "opencode"):
            candidate = shim.parent / "node_modules" / "@opencode" / "cli" / "bin" / name
            if candidate.is_file():
                return str(candidate)
    return str(shim)

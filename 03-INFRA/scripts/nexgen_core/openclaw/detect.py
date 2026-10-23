"""What this machine already has, asked in the least invasive way possible.

Every credential is only asked whether it EXISTS: nothing here opens a file
under a home except a package's own `package.json` and the configuration
OpenClaw already wrote. The two model backends are probed with a JSON GET
against a loopback URL through a client the caller injects, so a test answers
without opening a socket and a machine without Ollama costs one short timeout.

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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.paths import openclaw_config, resolve_engine_root, resolve_home, resolve_vault_data

#: Ollama's own default, and the variable that overrides it (Ollama reads it too).
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
#: llama.cpp's server default, and the engine's own variable for it.
DEFAULT_LLAMACPP_URL = "http://127.0.0.1:8080"

#: A probe answers fast or not at all: a stopped backend must never delay a command.
PROBE_TIMEOUT_SECONDS = 1.5
#: `npm root -g` is a local read, not a network call.
NPM_ROOT_TIMEOUT_SECONDS = 20.0

HttpJson = Callable[[str, float], Any]
Which = Callable[[str], "str | None"]
Run = Callable[..., Any]


@dataclass(frozen=True)
class Installation:
    """OpenClaw itself: its package, its version, and the file that configures it."""

    installed: bool
    version: str | None = None
    config_path: str = ""
    config_present: bool = False
    detail: str = ""


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
class LocalModels:
    """The models this machine serves itself, by provider, exactly as reported."""

    ollama_url: str = DEFAULT_OLLAMA_URL
    ollama: tuple[str, ...] = ()
    llamacpp_url: str = DEFAULT_LLAMACPP_URL
    llamacpp: tuple[str, ...] = ()
    detail: str = ""

    @property
    def available(self) -> bool:
        return bool(self.ollama or self.llamacpp)


@dataclass(frozen=True)
class Detection:
    """The whole answer: one snapshot, taken once, of what is on this machine."""

    openclaw: Installation
    claude: Login
    codex: Login
    opencode_go: bool
    local: LocalModels
    n8n: bool

    def to_dict(self) -> dict[str, Any]:
        """The snapshot as JSON-safe data, for `plan --json` and the doctor."""
        return {
            "openclaw": asdict(self.openclaw),
            "claude": {**asdict(self.claude), "available": self.claude.available},
            "codex": {**asdict(self.codex), "available": self.codex.available},
            "opencode_go": self.opencode_go,
            "local": {**asdict(self.local), "available": self.local.available},
            "n8n": self.n8n,
        }


def _http_json(url: str, timeout: float) -> Any:
    """One JSON GET against a local backend.

    A backend that is not running is a normal answer to a capability probe, not
    an error: the caller gets None and says "not reachable" in words.
    """
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, ValueError):  # nexgen: allow reason="a stopped local backend is an answer to a probe, not a failure"
        return None


def _read_json(path: Path) -> Any:
    """A JSON file, or None when it is absent or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable package.json means 'not installed', and the doctor names the path"
        return None


def _npm_root(run: Run, env: dict[str, str]) -> Path | None:
    """Where npm keeps its global packages, asked once and injectable in tests."""
    try:
        completed = run(
            ["npm", "root", "-g"], capture_output=True, text=True, check=False,
            timeout=NPM_ROOT_TIMEOUT_SECONDS, env=env, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):  # nexgen: allow reason="npm missing or wedged means 'cannot locate the package', which detection reports"
        return None
    lines = [line.strip() for line in str(getattr(completed, "stdout", "") or "").splitlines() if line.strip()]
    return Path(lines[-1]) if lines else None


def _detect_openclaw(home: Path, env: dict[str, str], run: Run | None, which: Which) -> Installation:
    """The package, its version from npm's own tree, and the configuration file."""
    config = openclaw_config(home, env)
    run = run or subprocess.run
    if which("npm") is None:
        return Installation(
            installed=False, config_path=str(config), config_present=config.is_file(),
            detail=t("npm is not on PATH, so a global OpenClaw cannot be found or installed"),
        )
    root = _npm_root(run, env)
    if root is None:
        return Installation(
            installed=False, config_path=str(config), config_present=config.is_file(),
            detail=t("npm did not say where its global packages live"),
        )
    package = root / "openclaw" / "package.json"
    manifest = _read_json(package) if package.is_file() else None
    if isinstance(manifest, dict) and manifest.get("version"):
        return Installation(
            installed=True, version=str(manifest["version"]),
            config_path=str(config), config_present=config.is_file(),
        )
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


def _base_url(raw: str | None, default: str) -> str:
    """A local backend URL, with the scheme Ollama's own variable usually omits."""
    value = (raw or default).strip().rstrip("/")
    return value if value.startswith(("http://", "https://")) else f"http://{value}"


def _probe_ollama(url: str, probe: HttpJson) -> tuple[str, ...]:
    """The model names Ollama reports, exactly as reported (a `:latest` is kept)."""
    answer = probe(f"{url}/api/tags", PROBE_TIMEOUT_SECONDS)
    models = answer.get("models") if isinstance(answer, dict) else None
    if not isinstance(models, list):
        return ()
    return tuple(str(item["name"]) for item in models if isinstance(item, dict) and item.get("name"))


def _probe_llamacpp(url: str, probe: HttpJson) -> tuple[str, ...]:
    """The model ids llama.cpp's OpenAI-compatible endpoint reports."""
    answer = probe(f"{url}/v1/models", PROBE_TIMEOUT_SECONDS)
    data = answer.get("data") if isinstance(answer, dict) else None
    if not isinstance(data, list):
        return ()
    return tuple(str(item["id"]) for item in data if isinstance(item, dict) and item.get("id"))


def _detect_local(env: dict[str, str], probe: HttpJson) -> LocalModels:
    """Both local backends, each with its own URL and its own short timeout."""
    ollama_url = _base_url(env.get("OLLAMA_HOST"), DEFAULT_OLLAMA_URL)
    llamacpp_url = _base_url(env.get("NEXGEN_LLAMACPP_URL"), DEFAULT_LLAMACPP_URL)
    ollama = _probe_ollama(ollama_url, probe)
    llamacpp = _probe_llamacpp(llamacpp_url, probe)
    detail = ""
    if not ollama and not llamacpp:
        detail = t("no local model backend answered")
    return LocalModels(ollama_url=ollama_url, ollama=ollama, llamacpp_url=llamacpp_url, llamacpp=llamacpp, detail=detail)


def _module_state(module_id: str, engine_root: Path, vault_data: Path, env: dict[str, str]) -> str:
    """What the engine says a module is here, through its own catalog API."""
    from nexgen_core.modules import derive_state, external_paths, load_catalog, load_state_file

    try:  # nexgen: allow reason="an unreadable catalog means 'not active', and the doctor reports the catalog itself"
        catalog = load_catalog(engine_root, external=external_paths(vault_data))
        states = derive_state(catalog, load_state_file(vault_data), env=env)
    except Exception:  # noqa: BLE001 - a detection cannot fail on a broken catalog
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

    return Detection(
        openclaw=_detect_openclaw(home_dir, values, run, resolved_which),
        claude=_detect_claude(home_dir, resolved_which, system),
        codex=_detect_codex(home_dir, values, resolved_which),
        opencode_go=bool(values.get("OPENCODE_API_KEY")),
        local=_detect_local(values, probe),
        n8n=_module_state("n8n", engine, vault, values) == "local",
    )

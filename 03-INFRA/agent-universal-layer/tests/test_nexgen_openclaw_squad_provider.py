"""Which provider a squad model names, and which API key the setup registers for it.

One job: pin that a model running on a local CLI backend (`agy`, Claude Code, Codex)
never has a provider key pasted into OpenClaw for it, while a plain provider model
keeps registering the key it needs.

The provider is the prefix before the slash, read the way `openclaw/terms.py` reads
it. A substring test reads `google-antigravity-cli/gemini-3.8-flash` as `google` and,
with `GEMINI_API_KEY` in the environment, pastes an AI Studio key for an agent that
should run on the local `agy` CLI -- the road `docs/openclaw.md` declares closed. A
ref without a prefix keeps being read by name (`gemini-2.5-flash` -> `google`).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexgen_core.i18n import set_language
from nexgen_core.openclaw.setup_steps import Setup
from nexgen_core.openclaw.squad import _infer_provider, register_model_keys

GEMINI_KEY = "gemini-test-key-not-a-secret"


@pytest.fixture(autouse=True)
def _restore_language():
    yield
    set_language(None)


def _setup(tmp_path: Path, env: dict[str, str]) -> Setup:
    """A setup whose every effect is visible here: no binary is really started."""
    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    return Setup(
        home=home,
        env=env,
        config_path=state / "openclaw.json",
        engine_root=None,
        vault_data=None,
        pinned=None,
        dry_run=False,
        out=MagicMock(),
        which=lambda cmd: "/bin/" + cmd,
        run=MagicMock(),
        service=MagicMock(),
        stamp="test",
    )


def _config(model: str) -> dict:
    return {"agents": {"entries": {"main": {"model": model}}}}


def _register(setup: Setup, model: str) -> tuple[int, MagicMock]:
    with patch("subprocess.run") as run:
        run.return_value = MagicMock(returncode=0)
        registered = register_model_keys(setup, _config(model), openclaw_cmd="/bin/openclaw")
    return registered, run


def _paste_api_key_commands(run: MagicMock) -> list[list[str]]:
    return [call.args[0] for call in run.call_args_list if "paste-api-key" in call.args[0]]


def _printed(setup: Setup) -> list[str]:
    return [str(call.args[0]) for call in setup.out.call_args_list]


def test_an_agy_model_never_gets_a_google_key(tmp_path: Path) -> None:
    """`google-antigravity-cli/...` is that backend, not a `google` model: no key of ours."""
    setup = _setup(tmp_path, {"GEMINI_API_KEY": GEMINI_KEY})

    registered, run = _register(setup, "google-antigravity-cli/gemini-3.8-flash")

    assert registered == 0
    assert _paste_api_key_commands(run) == []
    assert run.call_count == 0


def test_the_skip_on_agy_is_said_out_loud(tmp_path: Path) -> None:
    """A person must read why nothing was registered, in their own language."""
    setup = _setup(tmp_path, {"GEMINI_API_KEY": GEMINI_KEY})
    set_language("it")

    _register(setup, "google-antigravity-cli/gemini-3.8-flash")

    assert any(
        "gira sulla CLI locale google-antigravity-cli" in line for line in _printed(setup)
    ), _printed(setup)


def test_a_google_model_still_registers_its_key(tmp_path: Path) -> None:
    """The legitimate road stays open: `google/...` is the AI Studio key, pay-per-use."""
    setup = _setup(tmp_path, {"GEMINI_API_KEY": GEMINI_KEY})

    registered, run = _register(setup, "google/gemini-2.5-pro")

    assert registered == 1
    assert _paste_api_key_commands(run) == [
        ["/bin/openclaw", "models", "auth", "paste-api-key", "--agent", "main", "--provider", "google"]
    ]
    assert run.call_args.kwargs["input"] == GEMINI_KEY + "\n"


def test_a_model_without_a_prefix_is_read_by_name(tmp_path: Path) -> None:
    """`gemini-2.5-flash` names Google the way it did before: no prefix, same provider."""
    setup = _setup(tmp_path, {"GEMINI_API_KEY": GEMINI_KEY})

    registered, run = _register(setup, "gemini-2.5-flash")

    assert registered == 1
    assert _paste_api_key_commands(run)[0][-1] == "google"


def test_a_claude_cli_model_registers_no_anthropic_key(tmp_path: Path) -> None:
    """Claude Code keeps its own login: OpenClaw is not handed an Anthropic key for it."""
    setup = _setup(tmp_path, {"ANTHROPIC_API_KEY": "sk-ant-test"})

    registered, run = _register(setup, "claude-cli/claude-opus-5-5")

    assert registered == 0
    assert _paste_api_key_commands(run) == []


def test_an_anthropic_model_is_unchanged(tmp_path: Path) -> None:
    setup = _setup(tmp_path, {"ANTHROPIC_API_KEY": "sk-ant-test"})

    registered, run = _register(setup, "anthropic/claude-opus-5-5")

    assert registered == 1
    assert _paste_api_key_commands(run) == [
        ["/bin/openclaw", "models", "auth", "paste-api-key", "--agent", "main", "--provider", "anthropic"]
    ]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("google-antigravity-cli/gemini-3.8-flash", None),
        ("claude-cli/claude-opus-5-5", None),
        ("codex/gpt-6-luna", None),
        ("google/gemini-2.5-pro", ("google", "GEMINI_API_KEY")),
        ("gemini-2.5-flash", ("google", "GEMINI_API_KEY")),
        ("anthropic/claude-opus-5-5", ("anthropic", "ANTHROPIC_API_KEY")),
        ("openai/gpt-6-luna", ("openai", "OPENAI_API_KEY")),
        ("opencode-go/deepseek-v4", ("deepseek", "OPENCODE_GO_KEY")),
        ("ollama/qwen3:8b", None),
    ],
)
def test_the_provider_is_the_prefix_before_the_slash(model: str, expected: tuple[str, str] | None) -> None:
    assert _infer_provider(model) == expected

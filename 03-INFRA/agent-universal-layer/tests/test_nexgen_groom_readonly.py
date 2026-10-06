"""The grooming pass labelled read-only can run only the read-only scripts its prompt names."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.vault import prompts  # noqa: E402
from nexgen_core.vault.runner import ClaudeRunner  # noqa: E402


def _shell_rules(tools: list[str]) -> list[str]:
    return [t for t in tools if t.startswith("Bash(")]


def test_the_read_only_pass_gets_no_general_interpreter_or_git():
    for rule in _shell_rules(ClaudeRunner.READ_TOOLS):
        assert rule not in {"Bash(python3:*)", "Bash(git:*)", "Bash(mv:*)", "Bash(mkdir:*)"}
    assert not {"Edit", "Write"} & set(ClaudeRunner.READ_TOOLS)


def test_it_can_still_run_what_its_prompt_asks_for():
    prompt = prompts.build_propose_prompt("03-INFRA/vault-grooming-playbook.md", "/v")
    assert "vault-map" in prompt
    rules = _shell_rules(ClaudeRunner.READ_TOOLS)
    assert "Bash(vault-map:*)" in rules
    assert "Bash(python3 03-INFRA/scripts/vault-map.py:*)" in rules
    assert "Bash(python3 03-INFRA/scripts/vault-lifecycle-audit.py:*)" in rules


def test_every_script_the_read_only_rules_name_exists():
    repo = Path(__file__).resolve().parents[3]
    for rule in _shell_rules(ClaudeRunner.READ_TOOLS):
        if rule.startswith("Bash(python3 "):
            script = rule.removeprefix("Bash(python3 ").removesuffix(":*)")
            assert (repo / script).is_file(), script

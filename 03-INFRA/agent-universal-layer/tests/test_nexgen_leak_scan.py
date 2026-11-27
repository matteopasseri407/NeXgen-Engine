"""The anti-leak gate drives gitleaks, and never passes when it cannot.

gitleaks (MIT) does the scanning; this file pins what the engine still owns: which flag each
mode becomes, the private deny-list, the published-findings baseline, the redacted output and
the fail-closed refusals. Every test here drives a fake `gitleaks` on PATH, so the suite runs
on a machine where the real binary is not installed.
"""
from __future__ import annotations

import json
import os
import random
import re
import string
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

SCAN_DIR = Path(__file__).resolve().parents[1] / "leak-scan"
SCANNER = SCAN_DIR / "leak_scan.py"
CONFIG = SCAN_DIR / "gitleaks.toml"
BASELINE = SCAN_DIR / "gitleaks-baseline.json"

FAKE_GITLEAKS = '''\
"""A stand-in for gitleaks: records what it was asked, then plays a scripted answer."""
import json, os, pathlib, sys

here = pathlib.Path(os.environ["FAKE_GITLEAKS_DIR"])
argv = sys.argv[1:]
config = ""
if "--config" in argv:
    config = pathlib.Path(argv[argv.index("--config") + 1]).read_text(encoding="utf-8")
with open(here / "calls.jsonl", "a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": argv, "stdin": sys.stdin.read(), "config": config}) + "\\n")
failure = here / "fail.txt"
if failure.exists():
    sys.stderr.write(failure.read_text(encoding="utf-8"))
    sys.exit(2)
sys.stdout.write((here / "report.json").read_text(encoding="utf-8"))
sys.exit(int((here / "exit.txt").read_text(encoding="utf-8").strip()))
'''


def fake_gitleaks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a fake `gitleaks` first on PATH and return the folder it reads its script from."""
    home = tmp_path / "fake"
    home.mkdir()
    script = home / "fake_gitleaks.py"
    script.write_text(FAKE_GITLEAKS, encoding="utf-8")
    (home / "report.json").write_text("[]", encoding="utf-8")
    (home / "exit.txt").write_text("0", encoding="utf-8")
    if os.name == "nt":
        shim = home / "gitleaks.bat"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        shim = home / "gitleaks"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(home) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_GITLEAKS_DIR", str(home))
    return home


def answer(home: Path, findings: list[dict] | None = None, exit_code: int = 0) -> None:
    """What the fake binary reports on the next call."""
    (home / "report.json").write_text(json.dumps(findings or []), encoding="utf-8")
    (home / "exit.txt").write_text(str(exit_code), encoding="utf-8")


def calls(home: Path) -> list[dict]:
    path = home / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def scan(*args: str) -> subprocess.CompletedProcess:
    # PYTHONIOENCODING: the gate prints an ellipsis, and the Windows console codepage would
    # turn it into a byte the test cannot decode (the same reason the old suite set it).
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(SCANNER), *args], capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=180, check=False)


def a_finding(rule: str = "nexgen-aws-access-key", file: str = "notes.env", line: int = 2) -> dict:
    """One gitleaks finding, with a secret assembled at runtime so this file carries none."""
    secret = "AK" + "IA" + "A" * 16
    return {"RuleID": rule, "Description": "test", "File": file, "StartLine": line,
            "EndLine": line, "StartColumn": 1, "EndColumn": len(secret),
            "Match": secret, "Secret": secret}


def a_repo(tmp_path: Path, *messages: str) -> str:
    """A real git repo with one commit per message; returns the first commit's SHA."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t" + "@" + "example.invalid",
           "GIT_COMMITTER_EMAIL": "t" + "@" + "example.invalid"}
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True, capture_output=True, env=env)
    first = ""
    for index, message in enumerate(messages):
        (repo / f"file{index}.txt").write_text(f"{message}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True, env=env)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True, capture_output=True, env=env)
        if not first:
            first = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                                   capture_output=True, text=True, env=env).stdout.strip()
    return first


# --- provisioning: no binary means no scan, never a pass -------------------------------------


def test_a_missing_gitleaks_refuses_and_says_how_to_install_it(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    done = scan("--tree", str(tmp_path))

    assert done.returncode == 1
    assert "gitleaks is not on PATH" in done.stderr
    assert "winget install gitleaks" in done.stderr
    assert "scoop install gitleaks" in done.stderr
    assert "brew install gitleaks" in done.stderr
    assert "github.com/gitleaks/gitleaks/releases" in done.stderr


# --- one flag per mode -----------------------------------------------------------------------


def test_a_staged_scan_is_gitleaks_own_pre_commit_lane(tmp_path, monkeypatch):
    home = fake_gitleaks(tmp_path, monkeypatch)

    done = scan("--staged", "--repo", str(tmp_path))

    assert done.returncode == 0, done.stderr
    argv = calls(home)[0]["argv"]
    assert argv[:2] == ["git", "--staged"]
    assert "--pre-commit" in argv
    assert str(tmp_path) in argv
    assert argv[argv.index("--config") + 1] == str(CONFIG)
    assert argv[argv.index("--report-path") + 1] == "-", "the report must come back on stdout"
    assert "--baseline-path" not in argv, "a staged commit has no published history to exempt"


def test_a_tree_scan_asks_for_the_directory_mode_of_each_path(tmp_path, monkeypatch):
    home = fake_gitleaks(tmp_path, monkeypatch)
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()

    assert scan("--tree", str(first), str(second)).returncode == 0

    assert [call["argv"][0] for call in calls(home)] == ["dir", "dir"]
    assert [call["argv"][1] for call in calls(home)] == [str(first), str(second)]


def test_a_commit_range_uses_log_opts_and_scans_the_messages_gitleaks_ignores(tmp_path, monkeypatch):
    home = fake_gitleaks(tmp_path, monkeypatch)
    base = a_repo(tmp_path, "base commit", "second commit", "third commit")
    repo = tmp_path / "repo"

    done = scan("--repo", str(repo), "--commit-range", f"{base}..HEAD")

    assert done.returncode == 0, done.stderr
    history, *messages = calls(home)
    assert history["argv"][0] == "git"
    assert history["argv"][history["argv"].index("--log-opts") + 1] == f"{base}..HEAD"
    assert history["argv"][history["argv"].index("--baseline-path") + 1] == str(BASELINE)
    assert len(messages) == 2, "every commit in the range has its message scanned"
    assert {call["stdin"].strip() for call in messages} == {"second commit", "third commit"}
    assert all(call["argv"][0] == "stdin" for call in messages)


def test_a_message_file_is_scanned_through_stdin(tmp_path, monkeypatch):
    home = fake_gitleaks(tmp_path, monkeypatch)
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("subject line\n\nbody line\n", encoding="utf-8")

    assert scan("--message", str(message)).returncode == 0

    argv, stdin = calls(home)[0]["argv"], calls(home)[0]["stdin"]
    assert argv[0] == "stdin"
    assert stdin == "subject line\n\nbody line\n"


# --- the output contract ---------------------------------------------------------------------


def test_a_blocking_finding_is_reported_redacted_and_blocks(tmp_path, monkeypatch):
    home = fake_gitleaks(tmp_path, monkeypatch)
    answer(home, [a_finding()], exit_code=1)

    done = scan("--tree", str(tmp_path))

    assert done.returncode == 1
    assert "! notes.env:2  [rule:nexgen-aws-access-key]" in done.stdout
    assert "1 finding(s)" in done.stdout
    assert "AKIAAAA" not in done.stdout, "the flagged value must never be printed"
    assert "AK…AA (20 chars)" in done.stdout, "just enough of it to recognise the value"


def test_a_soft_rule_warns_without_blocking_the_commit(tmp_path, monkeypatch):
    home = fake_gitleaks(tmp_path, monkeypatch)
    answer(home, [a_finding(rule="nexgen-soft-personal-name")], exit_code=1)

    done = scan("--tree", str(tmp_path))

    assert done.returncode == 0
    assert "soft warnings (not blocking)" in done.stdout
    assert "? notes.env:2" in done.stdout
    assert "BLOCKING" not in done.stdout


def test_an_unreadable_report_fails_closed(tmp_path, monkeypatch):
    home = fake_gitleaks(tmp_path, monkeypatch)
    (home / "report.json").write_text("{not json", encoding="utf-8")

    done = scan("--tree", str(tmp_path))

    assert done.returncode != 0
    assert "ABORT" in done.stderr and "no readable report" in done.stderr


def test_a_broken_scan_fails_closed_instead_of_reporting_a_clean_tree(tmp_path, monkeypatch):
    home = fake_gitleaks(tmp_path, monkeypatch)
    (home / "fail.txt").write_text("fatal: not a git repository\n", encoding="utf-8")

    done = scan("--tree", str(tmp_path))

    assert done.returncode != 0
    assert "gitleaks failed with 2" in done.stderr
    assert "not a git repository" in done.stderr


def test_an_empty_report_after_a_partial_scan_is_not_a_clean_tree(tmp_path, monkeypatch):
    """gitleaks exits 1 when the scan was cut short; an empty report there means unknown."""
    home = fake_gitleaks(tmp_path, monkeypatch)
    answer(home, [], exit_code=1)

    done = scan("--tree", str(tmp_path))

    assert done.returncode != 0
    assert "could not finish the scan" in done.stderr


# --- the private deny-list keeps its boundary -------------------------------------------------


def test_the_private_denylist_becomes_literal_rules_in_a_temporary_config(tmp_path, monkeypatch):
    home = fake_gitleaks(tmp_path, monkeypatch)
    secret = "deny-" + "value-" + "987654"
    private = tmp_path / "deny.txt"
    private.write_text(f"# private values, one per line\n\n{secret}\n", encoding="utf-8")

    assert scan("--tree", str(tmp_path), "--denylist", str(private)).returncode == 0

    call = calls(home)[0]
    effective = Path(call["argv"][call["argv"].index("--config") + 1])
    recorded = tomllib.loads(call["config"])
    added = [rule for rule in recorded["rules"] if rule["id"].startswith("nexgen-private-denylist-")]
    assert effective != CONFIG, "the public rules alone cannot carry a real value"
    assert [rule["regex"] for rule in added] == [re.escape(secret)]
    assert len(recorded["rules"]) == len(shipped_rules()) + 1, "the public rules come along"
    assert not effective.exists(), "the temporary config must not outlive the scan"


def test_the_public_rules_are_passed_untouched_when_there_is_no_denylist(tmp_path, monkeypatch):
    home = fake_gitleaks(tmp_path, monkeypatch)

    assert scan("--tree", str(tmp_path)).returncode == 0

    call = calls(home)[0]
    assert call["argv"][call["argv"].index("--config") + 1] == str(CONFIG)
    assert call["config"] == CONFIG.read_text(encoding="utf-8")


def test_a_missing_denylist_aborts_instead_of_scanning_without_it(tmp_path, monkeypatch):
    fake_gitleaks(tmp_path, monkeypatch)

    done = scan("--tree", str(tmp_path), "--denylist", str(tmp_path / "absent.txt"))

    assert done.returncode != 0
    assert "denylist given but not found" in done.stderr


# --- the published-findings baseline ----------------------------------------------------------


def test_a_named_baseline_replaces_the_shipped_one(tmp_path, monkeypatch):
    home = fake_gitleaks(tmp_path, monkeypatch)
    base = a_repo(tmp_path, "only commit")
    mine = tmp_path / "mine.json"
    mine.write_text("[]", encoding="utf-8")

    assert scan("--repo", str(tmp_path / "repo"), "--commit-range", f"{base}..HEAD",
                "--baseline", str(mine)).returncode == 0

    argv = calls(home)[0]["argv"]
    assert argv[argv.index("--baseline-path") + 1] == str(mine)


def test_a_missing_baseline_aborts_instead_of_scanning_without_it(tmp_path, monkeypatch):
    fake_gitleaks(tmp_path, monkeypatch)
    base = a_repo(tmp_path, "only commit")

    done = scan("--repo", str(tmp_path / "repo"), "--commit-range", f"{base}..HEAD",
                "--baseline", str(tmp_path / "absent.json"))

    assert done.returncode != 0
    assert "baseline not found" in done.stderr


# --- the shipped policy: rules, soft prefix, baseline consistency -----------------------------


def shipped_rules() -> dict[str, dict]:
    return {rule["id"]: rule for rule in tomllib.loads(CONFIG.read_text(encoding="utf-8"))["rules"]}


def test_the_config_keeps_every_rule_the_gate_promises():
    assert set(shipped_rules()) == {
        "nexgen-personal-ipv4",
        "nexgen-personal-home-linux",
        "nexgen-personal-home-windows",
        "nexgen-personal-email",
        "nexgen-long-digits",
        "nexgen-long-hex",
        "nexgen-high-entropy-quoted",
        "nexgen-high-entropy-labelled",
        "nexgen-aws-access-key",
        "nexgen-pem-private-key",
        "nexgen-github-token",
        "nexgen-github-fine-grained-token",
        "nexgen-anthropic-api-key",
        "nexgen-openai-project-key",
        "nexgen-age-secret-key",
        "nexgen-huggingface-token",
        "nexgen-npm-token",
        "nexgen-google-api-key",
        "nexgen-openai-style-key",
        "nexgen-stripe-key",
        "nexgen-jwt",
        "nexgen-soft-personal-name",
    }


def test_no_pattern_uses_syntax_the_go_regex_engine_cannot_compile():
    """gitleaks compiles RE2: no lookbehind, no lookahead, no backreference."""
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    patterns = [rule["regex"] for rule in config["rules"]]
    patterns += [regex for entry in config["allowlists"] for regex in entry.get("regexes", [])]
    patterns += [rule.get("path", "") for rule in config["rules"]]

    forbidden = [(pattern, token) for pattern in patterns
                 for token in ("(?<", "(?=", "(?!", "\\1")
                 if token in pattern]

    assert forbidden == []


def _synthetic_tokens() -> dict[str, str]:
    """Built at runtime: this file must not itself look like a leak."""
    rng = random.Random(7)

    def body(length: int, alphabet: str = string.ascii_letters + string.digits) -> str:
        return "".join(rng.choice(alphabet) for _ in range(length))

    url_safe = string.ascii_letters + string.digits + "-_"
    return {
        "anthropic": "sk-" + "ant-api03-" + body(93, url_safe),
        "openai project": "sk-" + "proj-" + body(120, url_safe),
        "openai service account": "sk-" + "svcacct-" + body(80, url_safe),
        "github fine-grained": "github" + "_pat_" + body(22) + "_" + body(59),
        "github user token": "gh" + "u_" + body(36),
        "age secret key": "AGE-" + "SECRET-KEY-1" + body(58, string.ascii_uppercase + string.digits),
        "huggingface": "hf" + "_" + body(34),
        "npm": "npm" + "_" + body(36),
        "google api key": "AI" + "za" + body(35, url_safe),
    }


@pytest.mark.parametrize("provider", sorted(_synthetic_tokens()))
@pytest.mark.parametrize("template", ["API_KEY={tok}", '  "token": "{tok}",', "Authorization: Bearer {tok}"])
def test_the_rules_recognize_the_keys_of_common_providers(provider, template):
    """Every provider the gate promised to catch still has a rule that catches it."""
    rules = shipped_rules()
    line = template.format(tok=_synthetic_tokens()[provider])
    hits = [rule["id"] for rule in rules.values()
            if "regex" in rule and re.search(rule["regex"], line)]

    assert hits != [], f"{provider} slipped through in: {template}"


def test_the_config_keeps_the_soft_prefix_that_warns_instead_of_blocking():
    """The wrapper reads the prefix off the rule id; a renamed rule would block silently."""
    soft = [rule_id for rule_id in shipped_rules() if rule_id.startswith("nexgen-soft-")]

    assert soft == ["nexgen-soft-personal-name"]


def _released_by_rule_allowlist(rule: dict, secret: str) -> bool:
    """gitleaks' rule allowlist, default target: a regex that matches the captured secret."""
    return any(re.search(regex, secret)
               for entry in rule.get("allowlists", []) for regex in entry.get("regexes", []))


@pytest.mark.parametrize("rule_id", ["nexgen-high-entropy-quoted", "nexgen-high-entropy-labelled"])
@pytest.mark.parametrize("identifier", ["cacheCreationTokens", "ToastNotificationManager",
                                        "maxSkillsPromptChars", "WindowsPowerShell"])
def test_an_identifier_without_a_digit_is_not_a_credential(rule_id, identifier):
    """The retired rule asked for the Aa0 mix; a camelCase name between quotes never had it."""
    assert _released_by_rule_allowlist(shipped_rules()[rule_id], identifier)


@pytest.mark.parametrize("rule_id", ["nexgen-high-entropy-quoted", "nexgen-high-entropy-labelled"])
def test_a_token_with_the_aa0_mix_still_blocks(rule_id):
    token = string.ascii_letters[::3] + "a7Q"
    assert not _released_by_rule_allowlist(shipped_rules()[rule_id], token)


@pytest.mark.skipif(not __import__("shutil").which("gitleaks"), reason="needs the real gitleaks")
def test_the_real_scanner_passes_identifiers_and_catches_a_mixed_token(tmp_path):
    token = string.ascii_letters[1::3] + "9Kx"
    (tmp_path / "sample.py").write_text(
        'name = "cacheCreationTokens"\nhint = "ToastNotificationManager"\n'
        f'token = "{token}"\n', encoding="utf-8")
    report = tmp_path / "report.json"

    subprocess.run(["gitleaks", "dir", str(tmp_path / "sample.py"), "--config", str(CONFIG),
                    "--no-banner", "--report-format", "json", "--report-path", str(report)],
                   capture_output=True, text=True, check=False)

    lines = sorted({finding["StartLine"] for finding in json.loads(report.read_text(encoding="utf-8"))})
    assert lines == [3]


def test_the_deepseek_co_author_trailer_is_released_and_a_real_address_is_not():
    """The cheap workers sign their commits like Claude does: the vendor's noreply is not personal data."""
    policy = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    released = [pattern for entry in policy.get("allowlists", []) for pattern in entry.get("regexes", [])]

    def is_released(address: str) -> bool:
        return any(re.search(pattern, address) for pattern in released)

    assert is_released("noreply@deepseek.com")
    assert not is_released("someone" + "@deepseek.com")  # split so the scan does not flag the test itself

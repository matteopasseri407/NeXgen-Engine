"""The Google OAuth client is the user's own: the engine ships none."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO / "03-INFRA" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_local.connectors import auth  # noqa: E402
from nexgen_local.connectors.token_store import HttpResult  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    monkeypatch.setenv("WORKSPACE_MCP_TOKEN_DIR", str(token_dir))
    monkeypatch.delenv("WORKSPACE_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("WORKSPACE_GOOGLE_CLIENT_SECRET", raising=False)
    (token_dir / "tokens.json").write_text(json.dumps(
        {"refresh_token": "r", "access_token": "a", "expires_at": int(time.time()) - 10}), encoding="utf-8")
    return token_dir


def test_without_a_client_id_nothing_is_assumed(sandbox):
    assert auth.client_id() == ""
    ok, detail = auth.status()
    assert ok is False and "WORKSPACE_GOOGLE_CLIENT_ID" in detail


def test_a_refresh_without_a_client_id_says_what_to_set_and_sends_nothing(sandbox):
    sent = []
    with pytest.raises(auth.AuthError, match="WORKSPACE_GOOGLE_CLIENT_ID"):
        auth.access_token(http=lambda req, timeout: sent.append(req))
    assert sent == []


def test_the_users_own_client_id_is_the_one_sent(sandbox, monkeypatch):
    monkeypatch.setenv("WORKSPACE_GOOGLE_CLIENT_ID", "my-own-client")
    seen = []

    def http(req, timeout):
        seen.append(req.data.decode("utf-8"))
        return HttpResult(200, json.dumps({"access_token": "fresh", "expires_in": 3600}).encode("utf-8"))

    assert auth.access_token(http=http) == "fresh"
    assert "client_id=my-own-client" in seen[0]
    assert auth.status()[0] is True


def test_the_machine_local_env_file_works_too(sandbox):
    (sandbox / "env").write_text("WORKSPACE_GOOGLE_CLIENT_ID=from-the-file\n", encoding="utf-8")
    assert auth.client_id() == "from-the-file"


def test_no_google_client_id_is_committed_anywhere():
    """Built from parts so this file is not itself an instance of what it forbids."""
    shape = re.compile(r"\b\d{6,}-[a-z0-9]{8,}\." + "apps" + r"\." + "googleusercontent" + r"\.com")
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True,
                             encoding="utf-8", check=True).stdout.splitlines()
    offenders = []
    for name in tracked:
        path = REPO / name
        if path.suffix in {".png", ".jpg", ".gif", ".ico", ".whl", ".gz"} or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if shape.search(text):
            offenders.append(name)
    assert offenders == []

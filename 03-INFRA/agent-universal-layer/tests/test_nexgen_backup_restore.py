"""backup-restore.sh with a recording stand-in for docker: order of operations, not Docker itself."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "backup-restore.sh"

pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="a bash script for Linux hosts")

FAKE_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  ps) printf '%s' "${FAKE_RUNNING:-}" ;;
  run) if [ -n "${FAKE_RUN_FAILS:-}" ]; then exit 9; fi
       # a backup run: leave an archive where the script expects one
       if [[ "$*" == *"tar czf"* ]]; then
         name="$(printf '%s' "$*" | sed -n 's#.*tar czf /backup/\\([^ ]*\\) .*#\\1#p')"
         [ -n "$name" ] && : > "$BACKUP_DIR/$name"
       fi ;;
esac
exit 0
"""


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "docker.log"
    log.write_text("", encoding="utf-8")
    environment = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "BACKUP_DIR": str(tmp_path / "backups"),
    }
    return environment, log, tmp_path


def run(args, environment, stdin=""):
    return subprocess.run(["bash", str(SCRIPT), *args], env=environment, input=stdin,
                          capture_output=True, text=True, encoding="utf-8", check=False)


def calls(log: Path) -> list[str]:
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line]


def verbs(log: Path) -> list[str]:
    return [line.split()[0] for line in calls(log)]


def test_the_containers_using_a_volume_are_stopped_for_the_copy_and_started_again(env):
    environment, log, _ = env
    result = run(["backup", "n8n"], {**environment, "FAKE_RUNNING": "abc123\n"})
    assert result.returncode == 0, result.stderr
    sequence = verbs(log)
    first_stop = sequence.index("stop")
    assert sequence.index("run") > first_stop
    assert sequence.index("start") > sequence.index("run")
    assert "stop abc123" in calls(log) and "start abc123" in calls(log)


def test_a_failed_copy_still_starts_the_service_and_leaves_no_partial_archive(env):
    environment, log, tmp = env
    result = run(["backup", "n8n"], {**environment, "FAKE_RUNNING": "abc123\n", "FAKE_RUN_FAILS": "1"})
    assert result.returncode != 0
    assert "start abc123" in calls(log)
    assert list((tmp / "backups").glob("*.tar.gz")) == []


def test_hot_mode_does_not_stop_anything(env):
    environment, log, _ = env
    result = run(["backup", "n8n"], {**environment, "FAKE_RUNNING": "abc123\n", "BACKUP_HOT": "1"})
    assert result.returncode == 0, result.stderr
    assert "stop" not in verbs(log) and "start" not in verbs(log)


def test_nothing_running_means_nothing_to_stop(env):
    environment, log, _ = env
    assert run(["backup", "n8n"], environment).returncode == 0
    assert "stop" not in verbs(log)


def test_a_restore_refuses_while_a_container_still_uses_the_volume(env):
    environment, log, tmp = env
    archive = tmp / "n8n-data_x.tar.gz"
    archive.write_bytes(b"x")
    result = run(["restore", str(archive), "n8n-data"], {**environment, "FAKE_RUNNING": "abc123\n"}, stdin="yes\n")
    assert result.returncode == 1
    assert "refusing" in result.stderr
    assert "run" not in verbs(log)
    assert "volume" not in verbs(log)


def test_a_restore_passes_the_archive_name_as_an_argument_not_inside_the_shell_command(env):
    environment, log, tmp = env
    nasty = tmp / "it's; touch pwned #.tar.gz"
    nasty.write_bytes(b"x")
    result = run(["restore", str(nasty), "n8n-data"], environment, stdin="yes\n")
    assert result.returncode == 0, result.stderr
    run_call = next(line for line in calls(log) if line.startswith("run "))
    assert "it's; touch pwned #.tar.gz" in run_call
    assert run_call.index("sh -c") < run_call.index("it's;")
    assert 'tar xzf "/backup/$1"' in run_call
    assert not (tmp / "pwned").exists()

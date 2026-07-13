"""Regression tests for the "secrets at rest" hardening pass on
03-INFRA/deploy/: n8n backups (backup-restore.sh) and the GPG secrets
workflow documented in 99-SECRETS/README.md.

Findings covered (see the audit that produced this pass):
  A (HIGH)   n8n backups were unencrypted and N8N_ENCRYPTION_KEY was never
             set explicitly, so n8n auto-generated one INSIDE the backed-up
             volume -- a copy of the tarball exposed every credential n8n
             ever held, in plaintext.
  B (MEDIUM) backup archives were neither git-ignored nor permission
             restricted, unlike .env (already chmod 600 in bootstrap-vps.sh).
  C (MEDIUM) the documented GPG workflow decrypted the whole secrets
             archive to a predictable, world-readable /tmp/secrets.md path
             for the duration of every edit.

Docker itself is not available in this test environment (no daemon), so
the backup-permission regression uses a minimal shell stub on PATH that
emulates only the `docker run` / `docker volume` invocations
backup-restore.sh actually makes -- no real container is started.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "03-INFRA" / "deploy"
BOOTSTRAP = DEPLOY / "bootstrap-vps.sh"
BACKUP_RESTORE = DEPLOY / "backup-restore.sh"
ENV_EXAMPLE = DEPLOY / ".env.example"
N8N_COMPOSE = DEPLOY / "n8n" / "docker-compose.yml"
ROOT_GITIGNORE = REPO / ".gitignore"
SECRETS_README = REPO / "99-SECRETS" / "README.md"

WINDOWS_SKIP = pytest.mark.skipif(
    os.name == "nt", reason="POSIX permission bits are not the Windows permission model."
)


# --- Finding A: N8N_ENCRYPTION_KEY never set explicitly ------------------

def test_env_example_documents_n8n_encryption_key_as_blank_and_auto_generated():
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert re.search(r"^N8N_ENCRYPTION_KEY=\s*$", content, re.MULTILINE), (
        "N8N_ENCRYPTION_KEY must be present in .env.example and left BLANK "
        "(never a value the user is expected to invent or fill in by hand)"
    )
    assert "auto-generat" in content.lower(), (
        "expected .env.example to explain that N8N_ENCRYPTION_KEY is "
        "auto-generated, not user-supplied"
    )


def test_bootstrap_vps_generates_n8n_encryption_key_idempotently():
    content = BOOTSTRAP.read_text(encoding="utf-8")
    assert "openssl rand -hex 32" in content, (
        "expected bootstrap-vps.sh to generate secrets programmatically "
        "with openssl rand, never a value the user has to invent or remember"
    )
    assert "N8N_ENCRYPTION_KEY" in content
    # N8N_ENCRYPTION_KEY is generated via the shared ensure_env_secret()
    # helper (also used for FIRECRAWL_REDIS_PASSWORD), not a bespoke inline
    # block. Idempotence guard lives inside that helper: it only generates
    # when the existing value is empty, so re-running never clobbers a key
    # already in use (that would brick every credential n8n holds).
    assert "ensure_env_secret N8N_ENCRYPTION_KEY" in content, (
        "expected N8N_ENCRYPTION_KEY to be generated via the shared "
        "ensure_env_secret() helper"
    )
    helper_start = content.index("ensure_env_secret()")
    helper_body_end = content.index("ensure_env_secret N8N_ENCRYPTION_KEY")
    helper_body = content[helper_start:helper_body_end]
    assert 'if [ -n "$current" ]; then' in helper_body, (
        "expected ensure_env_secret() to check for a *non-empty* existing "
        "value before generating a new one"
    )


def test_n8n_compose_passes_encryption_key_to_the_container():
    content = N8N_COMPOSE.read_text(encoding="utf-8")
    live_lines = [
        line for line in content.splitlines()
        if not line.strip().startswith("#")
    ]
    assert any("N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY}" in line for line in live_lines), (
        "N8N_ENCRYPTION_KEY must be wired into the n8n container's "
        "environment block, uncommented — .env.example/bootstrap-vps.sh "
        "generating the value is useless if compose never passes it through"
    )


def test_bootstrap_vps_key_generation_runs_after_env_chmod():
    """The key-generation call must run after .env is confirmed to exist
    and chmod 600'd, and the shared helper it calls must re-chmod 600 after
    rewriting the file (a bare `>>`/`mv` can reset the mode depending on
    umask)."""
    content = BOOTSTRAP.read_text(encoding="utf-8")
    env_chmod_idx = content.index("chmod 600 .env")
    call_idx = content.index("ensure_env_secret N8N_ENCRYPTION_KEY")
    assert call_idx > env_chmod_idx, (
        "N8N_ENCRYPTION_KEY generation should come after the .env "
        "existence/chmod check"
    )
    # The shared helper itself must re-tighten permissions (it rewrites the
    # file) -- checked on the helper's own body, not by text position
    # relative to the call site, since the helper is defined once and
    # called for multiple secrets.
    helper_start = content.index("ensure_env_secret()")
    helper_end = content.index("\n}\n", helper_start)
    helper_body = content[helper_start:helper_end]
    assert "chmod 600 .env" in helper_body, (
        "expected ensure_env_secret() to re-chmod 600 .env after rewriting it"
    )


# --- Finding B: backup archives unrestricted and untracked ---------------

def test_do_backup_one_chmods_dir_and_archive_in_source():
    """Static check: the chmod calls must exist in do_backup_one, in the
    right order (dir tightened before docker writes into it isn't
    possible since docker creates the dir's contents, but the dir itself
    must be 700 and the archive must be chmod 600 immediately after
    docker writes it — no permissive window)."""
    content = BACKUP_RESTORE.read_text(encoding="utf-8")
    fn_start = content.index("do_backup_one() {")
    fn_end = content.index("\n}", fn_start)
    body = content[fn_start:fn_end]
    assert re.search(r"chmod\s+700\s+\"\$BACKUP_DIR\"", body), (
        "expected do_backup_one to chmod 700 $BACKUP_DIR"
    )
    assert re.search(r"chmod\s+600\s+\"\$BACKUP_DIR/\$archive_name\"", body), (
        "expected do_backup_one to chmod 600 the archive right after creation"
    )
    # Order: the archive chmod must come after the docker run that creates it.
    docker_idx = body.index("docker run")
    chmod_archive_idx = body.index("chmod 600")
    assert chmod_archive_idx > docker_idx, (
        "archive chmod must happen AFTER the archive is created, not before"
    )


def test_backup_restore_warns_that_backups_are_sensitive():
    content = BACKUP_RESTORE.read_text(encoding="utf-8")
    assert "sensitive" in content.lower(), (
        "expected an explicit warning that a backup archive is as "
        "sensitive as any n8n credential"
    )


def test_root_gitignore_excludes_deploy_backups():
    content = ROOT_GITIGNORE.read_text(encoding="utf-8")
    assert "03-INFRA/deploy/backups/" in content, (
        "backup archives must be git-ignored at the repo root, the same "
        "way .env already is"
    )


@WINDOWS_SKIP
def test_backup_restore_actually_produces_restricted_permissions(tmp_path):
    """Dynamic regression: run backup-restore.sh's backup path against a
    stub `docker` on PATH (no real daemon needed) and verify the resulting
    directory and archive really do end up at 0700/0600, not just that the
    chmod calls exist in the source."""
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    docker_stub = stub_dir / "docker"
    docker_stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [ \"$1\" = run ]; then\n"
        "  shift\n"
        "  backup_host=\"\"\n"
        "  args=(\"$@\")\n"
        "  i=0\n"
        "  while [ $i -lt ${#args[@]} ]; do\n"
        "    if [ \"${args[$i]}\" = -v ]; then\n"
        "      i=$((i+1))\n"
        "      mount=\"${args[$i]}\"\n"
        "      case \"$mount\" in\n"
        "        *:/backup) backup_host=\"${mount%:/backup}\" ;;\n"
        "      esac\n"
        "    fi\n"
        "    i=$((i+1))\n"
        "  done\n"
        "  for a in \"${args[@]}\"; do\n"
        "    case \"$a\" in\n"
        "      /backup/*.tar.gz)\n"
        "        name=\"${a#/backup/}\"\n"
        "        : > \"$backup_host/$name\"\n"
        # Simulate the permissive default a container's own umask would
        # otherwise leave behind (this is exactly what the fix must undo).
        "        chmod 644 \"$backup_host/$name\"\n"
        "        ;;\n"
        "    esac\n"
        "  done\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    docker_stub.chmod(docker_stub.stat().st_mode | stat.S_IEXEC)

    backup_dir = tmp_path / "backups"
    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}:{env.get('PATH', '')}"
    env["BACKUP_DIR"] = str(backup_dir)

    result = subprocess.run(
        ["bash", str(BACKUP_RESTORE), "backup", "n8n"],
        cwd=DEPLOY,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"backup-restore.sh failed:\n{result.stdout}\n{result.stderr}"

    assert backup_dir.is_dir(), f"expected {backup_dir} to be created"
    dir_mode = stat.S_IMODE(backup_dir.stat().st_mode)
    assert dir_mode == 0o700, f"expected $BACKUP_DIR mode 0700, got {oct(dir_mode)}"

    archives = sorted(backup_dir.glob("n8n-data_*.tar.gz"))
    assert archives, f"expected an n8n-data_*.tar.gz archive in {backup_dir}"
    archive_mode = stat.S_IMODE(archives[0].stat().st_mode)
    assert archive_mode == 0o600, f"expected archive mode 0600, got {oct(archive_mode)}"


# --- restore path (beta-readiness review, 2026-07-13) ---------------------
# Only `backup` had a dynamic test before this; `cmd_restore` -- the
# destructive half, wipes a volume's current content -- had never been
# exercised by anything beyond shellcheck. Same no-real-daemon-needed stub
# philosophy as the backup test above, extended to `docker volume create`
# and the restore's own `sh -c "rm -rf ...; tar xzf ..."` invocation: a
# real host directory stands in for the named docker volume, and the stub
# actually performs the wipe + extract for real, so the test proves the
# restore logic works, not just that the right docker flags are present in
# the source.

def _restore_docker_stub(stub_dir: Path) -> None:
    docker_stub = stub_dir / "docker"
    docker_stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "STAND_IN=\"${VOLUME_STAND_IN_ROOT:?VOLUME_STAND_IN_ROOT must be set}\"\n"
        "if [ \"$1\" = volume ] && [ \"$2\" = create ]; then\n"
        "  mkdir -p \"$STAND_IN/$3\"\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = run ]; then\n"
        "  shift\n"
        "  volume_name=\"\"\n"
        "  backup_host=\"\"\n"
        "  args=(\"$@\")\n"
        "  i=0\n"
        "  while [ $i -lt ${#args[@]} ]; do\n"
        "    if [ \"${args[$i]}\" = -v ]; then\n"
        "      i=$((i+1))\n"
        "      mount=\"${args[$i]}\"\n"
        "      case \"$mount\" in\n"
        "        *:/volume) volume_name=\"${mount%:/volume}\" ;;\n"
        "        *:/backup:ro) backup_host=\"${mount%:/backup:ro}\" ;;\n"
        "      esac\n"
        "    fi\n"
        "    i=$((i+1))\n"
        "  done\n"
        "  volume_host=\"$STAND_IN/$volume_name\"\n"
        "  mkdir -p \"$volume_host\"\n"
        "  archive=\"$(ls \"$backup_host\"/*.tar.gz 2>/dev/null | head -n1)\"\n"
        "  rm -rf \"${volume_host:?}\"/* \"${volume_host:?}\"/.[!.]* 2>/dev/null || true\n"
        "  if [ -n \"$archive\" ]; then tar xzf \"$archive\" -C \"$volume_host\"; fi\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    docker_stub.chmod(docker_stub.stat().st_mode | stat.S_IEXEC)


@WINDOWS_SKIP
def test_backup_restore_restore_replaces_volume_content_after_confirmation(tmp_path):
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    _restore_docker_stub(stub_dir)

    stand_in_root = tmp_path / "volumes"
    volume_stand_in = stand_in_root / "n8n-data-test"
    volume_stand_in.mkdir(parents=True)
    (volume_stand_in / "stale-file.txt").write_text("must be gone after restore\n", encoding="utf-8")

    archive_dir = tmp_path / "backups"
    archive_dir.mkdir()
    restore_source = tmp_path / "restore-source"
    restore_source.mkdir()
    (restore_source / "database.sqlite").write_text("restored content\n", encoding="utf-8")
    archive = archive_dir / "n8n-data_20260713T000000Z.tar.gz"
    subprocess.run(
        ["tar", "czf", str(archive), "-C", str(restore_source), "."],
        check=True, capture_output=True,
    )

    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}:{env.get('PATH', '')}"
    env["VOLUME_STAND_IN_ROOT"] = str(stand_in_root)

    result = subprocess.run(
        ["bash", str(BACKUP_RESTORE), "restore", str(archive), "n8n-data-test"],
        cwd=DEPLOY, env=env, input="yes\n", capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 0, f"restore failed:\n{result.stdout}\n{result.stderr}"
    assert not (volume_stand_in / "stale-file.txt").exists(), "old volume content must be wiped, not merged"
    assert (volume_stand_in / "database.sqlite").read_text(encoding="utf-8") == "restored content\n"


@WINDOWS_SKIP
def test_backup_restore_restore_aborts_without_explicit_yes(tmp_path):
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    _restore_docker_stub(stub_dir)

    stand_in_root = tmp_path / "volumes"
    volume_stand_in = stand_in_root / "n8n-data-test"
    volume_stand_in.mkdir(parents=True)
    (volume_stand_in / "must-survive.txt").write_text("untouched\n", encoding="utf-8")

    archive_dir = tmp_path / "backups"
    archive_dir.mkdir()
    archive = archive_dir / "n8n-data_20260713T000000Z.tar.gz"
    archive.write_bytes(b"")  # never read: the abort must happen before docker is ever invoked

    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}:{env.get('PATH', '')}"
    env["VOLUME_STAND_IN_ROOT"] = str(stand_in_root)

    result = subprocess.run(
        ["bash", str(BACKUP_RESTORE), "restore", str(archive), "n8n-data-test"],
        cwd=DEPLOY, env=env, input="not-yes\n", capture_output=True, text=True, timeout=30,
    )

    assert result.returncode != 0
    assert "aborted" in result.stdout
    assert (volume_stand_in / "must-survive.txt").read_text(encoding="utf-8") == "untouched\n"


def test_backup_restore_restore_rejects_a_missing_archive_before_any_docker_call(tmp_path):
    # `require docker` runs before dispatch regardless of subcommand, so a
    # docker stub still needs to exist on PATH -- it must just never be
    # CALLED for this case. Make it fail loudly if it ever is.
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    docker_stub = stub_dir / "docker"
    docker_stub.write_text("#!/usr/bin/env bash\necho 'docker should never be invoked here' >&2\nexit 1\n")
    docker_stub.chmod(docker_stub.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}:{env.get('PATH', '')}"

    result = subprocess.run(
        ["bash", str(BACKUP_RESTORE), "restore", str(tmp_path / "does-not-exist.tar.gz"), "n8n-data-test"],
        cwd=DEPLOY, env=env, capture_output=True, text=True, timeout=30,
    )

    assert result.returncode != 0
    assert "no such backup file" in result.stderr


# --- Finding C: /tmp/secrets.md decrypted world-readable ------------------

def test_secrets_readme_never_redirects_gpg_straight_to_a_bare_tmp_path():
    content = SECRETS_README.read_text(encoding="utf-8")
    # The old bug: `gpg -d ... > /tmp/secrets.md` with no prior mktemp/chmod
    # anywhere in the workflow block.
    assert not re.search(r">\s*/tmp/secrets\.md\b", content), (
        "the GPG workflow must not redirect straight to a bare, "
        "predictable /tmp/secrets.md path — that file is created "
        "world-readable for the duration of every edit"
    )


def test_secrets_readme_uses_mktemp_with_explicit_chmod_before_gpg_writes():
    content = SECRETS_README.read_text(encoding="utf-8")
    assert "mktemp" in content, "expected the workflow to use mktemp for the plaintext scratch file"
    mktemp_idx = content.index("mktemp")
    chmod_idx = content.index("chmod 600", mktemp_idx)
    gpg_decrypt_idx = content.index("gpg -d")
    assert mktemp_idx < chmod_idx < gpg_decrypt_idx, (
        "the temp file must be created and chmod 600'd BEFORE gpg -d "
        "writes any plaintext into it, not after"
    )


def test_secrets_readme_still_documents_symmetric_gpg_and_shred():
    """Guard against accidentally changing the actual crypto flow while
    fixing the permission window — no new passphrase/manual step for the
    user, same gpg -c / shred -u ending as before."""
    content = SECRETS_README.read_text(encoding="utf-8")
    assert "gpg -c" in content
    assert "shred -u" in content

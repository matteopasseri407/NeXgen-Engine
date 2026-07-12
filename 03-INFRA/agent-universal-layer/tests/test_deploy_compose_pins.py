"""Regression tests for the Cloud-Server deploy profile's reproducibility.

Covers the NX-07 audit findings for 03-INFRA/deploy/: pinned (non-"latest")
image tags with coherent healthchecks in the three docker-compose.yml
files, and a POSIX-correct, Compose-v2 bootstrap-vps.sh.

Docker itself is not available in this test environment (no daemon, no
registry access), so compose validity is checked with PyYAML rather than
`docker compose config` — CI adds that step separately.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "03-INFRA" / "deploy"
COMPOSE_FILES = {
    "n8n": DEPLOY / "n8n" / "docker-compose.yml",
    "ocr": DEPLOY / "ocr" / "docker-compose.yml",
    "firecrawl": DEPLOY / "firecrawl" / "docker-compose.yml",
}
BOOTSTRAP = DEPLOY / "bootstrap-vps.sh"

# A real, explicit version tag: at least major.minor, optionally
# .patch/-suffix (e.g. 2.29.10, 1.0.0, 8.2.7-alpine). Rejects "latest" and
# other floating tags.
VERSION_TAG = re.compile(r"^\d+(\.\d+){1,2}(-[A-Za-z0-9][A-Za-z0-9.]*)?$")

# Narrow, documented exception: verified 2026-07-12 that ghcr.io/firecrawl/
# playwright-service publishes NO version-numbered tag at all (only
# latest/linux-amd64/buildcache variants) -- there is no floating-vs-pinned
# choice to make here, "latest" is the only tag that exists. Real
# reproducibility for this one service has to come from a pinned sha256
# digest (documented in the compose file's header comment), not a tag.
FLOATING_TAG_EXCEPTIONS = {("firecrawl", "firecrawl-playwright")}


def _load_compose(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _image_tag(image_ref: str) -> str:
    """Extracts the tag from an image reference, including the
    ${VAR:-repo/image:tag} form used by every service here."""
    tag = image_ref.rsplit(":", 1)[-1]
    return tag.rstrip("}")


def test_all_compose_files_load_with_pyyaml():
    for name, path in COMPOSE_FILES.items():
        assert path.is_file(), f"{name}: compose file missing at {path}"
        data = _load_compose(path)
        assert "services" in data, f"{name}: no top-level services key"
        assert data["services"], f"{name}: services block is empty"


def test_no_compose_image_uses_a_latest_tag():
    for name, path in COMPOSE_FILES.items():
        data = _load_compose(path)
        for service, cfg in data["services"].items():
            if (name, service) in FLOATING_TAG_EXCEPTIONS:
                continue
            image = cfg.get("image")
            assert image, f"{name}/{service}: no image key"
            tag = _image_tag(image)
            assert tag != "latest", f"{name}/{service}: image pinned to :latest ({image!r})"
            assert VERSION_TAG.match(tag), (
                f"{name}/{service}: tag {tag!r} does not look like an explicit "
                f"version (image={image!r})"
            )


def test_floating_tag_exception_is_actually_floating_and_documented():
    """The exception list must stay narrow: each entry really has no
    versioned tag upstream (still `latest` today) and the compose file
    documents why, so the exception doesn't silently rot into a plain
    unpinned image nobody explains."""
    for name, service in FLOATING_TAG_EXCEPTIONS:
        data = _load_compose(COMPOSE_FILES[name])
        image = data["services"][service]["image"]
        assert _image_tag(image) == "latest", (
            f"{name}/{service} is listed as a floating-tag exception but its "
            f"tag is {_image_tag(image)!r}, not 'latest' -- either upstream "
            f"started publishing versions (remove the exception and pin one) "
            f"or this entry is stale"
        )
        content = COMPOSE_FILES[name].read_text(encoding="utf-8")
        assert "no versioned tags" in content or "NO versioned tags" in content, (
            f"{name}/{service}'s floating tag must stay explained in the "
            f"compose file's header comment"
        )


def test_every_service_has_a_coherent_healthcheck():
    required_keys = {"test", "interval", "timeout", "retries"}
    for name, path in COMPOSE_FILES.items():
        data = _load_compose(path)
        for service, cfg in data["services"].items():
            healthcheck = cfg.get("healthcheck")
            assert healthcheck, f"{name}/{service}: no healthcheck"
            missing = required_keys - healthcheck.keys()
            assert not missing, f"{name}/{service}: healthcheck missing {missing}"
            test = healthcheck["test"]
            assert isinstance(test, list) and test, f"{name}/{service}: empty healthcheck test"


def test_bootstrap_vps_has_a_bash_shebang():
    first_line = BOOTSTRAP.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/usr/bin/env bash", f"unexpected shebang line: {first_line!r}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable bits are not the Windows permission model.")
def test_bootstrap_vps_is_executable():
    mode = BOOTSTRAP.stat().st_mode
    assert mode & stat.S_IXUSR, "bootstrap-vps.sh must be executable"


def test_bootstrap_vps_has_strict_mode():
    content = BOOTSTRAP.read_text(encoding="utf-8")
    assert "set -euo pipefail" in content


def test_bootstrap_vps_locks_down_env_permissions():
    """Regression: .env.example is world-readable on purpose (placeholder
    values, public repo). A plain `cp .env.example .env` inherits that, so
    the REAL secrets a user fills into .env can stay world-readable unless
    something tightens it explicitly -- the same bug found and fixed on a
    real deployment, 2026-07-12, in a sibling override file."""
    content = BOOTSTRAP.read_text(encoding="utf-8")
    assert re.search(r"if\s+\[\s+-f\s+\.env\s+\]", content), "expected a check that .env actually exists"
    assert re.search(r"chmod\s+600\s+\.env\b", content), "expected an explicit chmod 600 on .env"


def test_bootstrap_vps_uses_compose_v2_not_legacy():
    content = BOOTSTRAP.read_text(encoding="utf-8")
    # The original bug: `docker-compose -f <file> up ...` (legacy v1 CLI).
    # Matched narrowly on "-f" so this doesn't flag prose that merely
    # mentions the legacy command name (e.g. an unsupported-tool message)
    # or the unrelated `docker-compose.yml` filenames.
    legacy_invocations = re.findall(r"docker-compose\s+-f\b", content)
    assert not legacy_invocations, f"found legacy docker-compose invocation(s): {legacy_invocations}"
    assert re.search(r"docker compose\s+-f\b", content), "expected at least one `docker compose -f` (v2) invocation"
    assert re.search(r"n8n/docker-compose\.yml", content)
    assert re.search(r"firecrawl/docker-compose\.yml", content)
    assert re.search(r"ocr/docker-compose\.yml", content)


def test_bootstrap_vps_passes_env_file_to_every_stack():
    """Regression, confirmed live on the real VPS 2026-07-12: docker
    compose's default .env lookup is relative to the directory of the
    FIRST -f file (e.g. n8n/), not this script's cwd. A bare
    `docker compose -f n8n/docker-compose.yml up` silently ignores the
    .env sitting next to this script and every ${VAR} falls back to its
    compose-file default -- dropping secrets with no error at all.
    --env-file must be passed explicitly on every `docker compose -f`
    invocation that brings a stack up."""
    up_invocations = [
        line for line in BOOTSTRAP.read_text(encoding="utf-8").splitlines()
        if re.search(r"docker compose -f \S+\.yml", line) and re.search(r"\bup\b", line)
        and not line.strip().startswith(("#", "echo"))
    ]
    assert up_invocations, "expected at least one real `docker compose -f ... up` invocation"
    for line in up_invocations:
        assert "--env-file" in line, f"missing --env-file: {line!r}"


def test_backup_restore_rollback_passes_env_file():
    """Same bug, same fix, in the rollback path of backup-restore.sh."""
    content = (DEPLOY / "backup-restore.sh").read_text(encoding="utf-8")
    rollback_invocations = [
        line for line in content.splitlines()
        if re.search(r"docker compose -f \S", line) and re.search(r"\bup\b", line)
        and not line.strip().startswith(("#", "echo"))
    ]
    assert rollback_invocations, "expected a real `docker compose -f ... up` in the rollback path"
    for line in rollback_invocations:
        assert "--env-file" in line, f"missing --env-file: {line!r}"


# --- P7 audit finding (MEDIUM): no host firewall baseline -----------------
#
# 127.0.0.1 binding in each docker-compose.yml is the only thing standing
# between these services and the public internet. bootstrap-vps.sh must
# add a second, independent layer: a ufw baseline (deny incoming by
# default, SSH always allowed), applied only if ufw is present, and never
# in an order that could lock out the SSH session running the script.

def _bootstrap_lines():
    """Real (non-comment) lines only, so a comment that merely *mentions*
    a ufw command (e.g. explaining why ordering matters) can't masquerade
    as the actual invocation when checking ordering."""
    return [
        line for line in BOOTSTRAP.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    ]


def _first_index(lines, pattern):
    for i, line in enumerate(lines):
        if re.search(pattern, line):
            return i
    return None


def test_bootstrap_vps_degrades_gracefully_without_ufw():
    """Must not hard-require ufw: check with `command -v`, not `require()`
    (which exits 1), so boxes without ufw still deploy the stacks, just
    with a visible warning."""
    content = BOOTSTRAP.read_text(encoding="utf-8")
    assert re.search(r"command -v ufw\b", content), "expected a `command -v ufw` presence check"
    assert not re.search(r"require\s+ufw\b", content), (
        "must not use require() for ufw -- that exits 1 and blocks deploy "
        "entirely on boxes without ufw"
    )
    assert re.search(r"(?i)warn", content), "expected a visible warning when ufw is missing"


def test_bootstrap_vps_ufw_baseline_rules_present():
    content = BOOTSTRAP.read_text(encoding="utf-8")
    assert re.search(r"ufw\s+allow\s+OpenSSH\b", content), "expected 'ufw allow OpenSSH'"
    assert re.search(r"ufw\s+default\s+deny\s+incoming\b", content), "expected 'ufw default deny incoming'"
    assert re.search(r"ufw\s+default\s+allow\s+outgoing\b", content), "expected 'ufw default allow outgoing'"
    assert re.search(r"ufw\s+(--force\s+enable|enable\s+--force)\b", content), (
        "expected 'ufw enable' with --force (non-interactive, safe for automation)"
    )


def test_bootstrap_vps_ufw_allows_ssh_before_denying_incoming():
    """The SSH allow rule must be applied before default-deny/enable, or a
    fresh `ufw enable` on a box with no prior rules can cut off the very
    SSH session running this script."""
    lines = _bootstrap_lines()
    allow_ssh_idx = _first_index(lines, r"ufw\s+allow\s+OpenSSH\b")
    deny_incoming_idx = _first_index(lines, r"ufw\s+default\s+deny\s+incoming\b")
    enable_idx = _first_index(lines, r"ufw\s+(--force\s+enable|enable\s+--force)\b")

    assert allow_ssh_idx is not None, "expected 'ufw allow OpenSSH'"
    assert deny_incoming_idx is not None, "expected 'ufw default deny incoming'"
    assert enable_idx is not None, "expected 'ufw enable'"

    assert allow_ssh_idx < deny_incoming_idx, (
        "'ufw allow OpenSSH' must appear before 'ufw default deny incoming' "
        "to avoid locking out the running SSH session"
    )
    assert allow_ssh_idx < enable_idx, (
        "'ufw allow OpenSSH' must appear before 'ufw enable' "
        "to avoid locking out the running SSH session"
    )


def test_bootstrap_vps_ufw_baseline_documented_as_minimum():
    """Finding explicitly requires this be documented as a minimum
    baseline, not an enterprise firewall, so operators do not mistake it
    for full hardening."""
    content = BOOTSTRAP.read_text(encoding="utf-8")
    assert re.search(r"(?i)minimum baseline", content), (
        "expected the ufw step to document itself as a minimum baseline"
    )
    assert re.search(r"(?i)not.{0,40}enterprise", content), (
        "expected an explicit disclaimer that this is not an enterprise firewall"
    )

"""vault-groom: one orchestrated grooming pass over the vault.

`preview` (the default) is read-only and never writes anything. `apply` is
the guarded flow: propose (read-only), show the tranche, require "yes",
execute inside a throwaway clone with no `origin`, and promote into the
real vault only if the audit (`audit.py`) is clean. The safety rules live
in `gate.py`; this module lines them up in the right order.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.paths import resolve_engine_root, resolve_home, resolve_vault_data
from nexgen_core.vault import audit, gate, prompts
from nexgen_core.vault.git_utils import GitCommandError, git
from nexgen_core.vault.runner import Runner, RunnerError, get_runner

PLAYBOOK = "03-INFRA/vault-grooming-playbook.md"
DEFAULT_ARCHIVE_ROOT = "99-ARCHIVE"
DEFAULT_RUNNER = "claude"
DEFAULT_MODEL = "claude-sonnet-5"

RETIRED_MODES = {
    "plan": "vault-groom: 'plan' is retired -- use 'preview' (or run with no argument, same thing).",
    "run": "vault-groom: 'run'/'guarded' is retired -- use 'apply'.",
    "guarded": "vault-groom: 'run'/'guarded' is retired -- use 'apply'.",
}

def usage() -> str:
    """Built lazily, never as a module-level constant: the reader's language
    is only known once `t()` actually runs, not at import time."""
    return t(
        "usage: vault-groom [preview|apply]\n"
        "\n"
        "  (default / preview)  read-only: proposes one grooming tranche and stops.\n"
        "                        Never prompts, never writes -- always safe to run.\n"
        "  apply                 the guarded flow: propose (read-only), show you the\n"
        "                        tranche, require a typed 'yes', then execute exactly\n"
        "                        that tranche inside a throwaway clone with no\n"
        "                        remote, and promote it into the real vault only if\n"
        "                        the audit afterward is clean.\n"
        "\n"
        "Environment:\n"
        "  GROOM_RUNNER       claude|codex|agy (default: claude)\n"
        "  GROOM_MODEL        model name passed to the runner (default: claude-sonnet-5)\n"
        "  GROOM_NOPUSH=1     after a clean apply, keep the promoted commits local\n"
        "                     (skip the auto-publish step)\n"
        "  GROOM_LOG          override the preview/propose-pass log path\n"
        "  GROOM_STATE_DIR    where clones and audit records land\n"
        "                     (default: ~/.local/state/vault-groom)\n"
        "  AGENT_VAULT_DATA / KNOWLEDGE_VAULT_PATH   where the vault lives\n"
    )


def resolve_archive_root(vault: Path, playbook_rel: str = PLAYBOOK) -> str:
    """Reads it from the vault playbook (`archive_root: <path>` line)."""
    playbook_path = vault / playbook_rel
    if playbook_path.is_file():
        for line in playbook_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("archive_root:"):
                value = stripped.split(":", 1)[1].strip().strip("'\"")
                if value:
                    return value
    return DEFAULT_ARCHIVE_ROOT


def make_log_path(suffix: str) -> Path:
    """An unpredictable log path (mkstemp, not a timestamp name)."""
    fd, name = tempfile.mkstemp(suffix=f"{suffix}.log", prefix="vault-groom-")
    os.close(fd)
    return Path(name)


def preview(
    *, vault: Path, runner: Runner, log_path: Path | None = None, output: Callable[[str], object] = print
) -> int:
    """The default: proposes a tranche, shows it, writes nothing."""
    prompt = prompts.build_propose_prompt(PLAYBOOK, str(vault))
    result = runner.run_readonly(prompt)
    log_path = log_path or make_log_path(".preview")
    log_path.write_text(result.text, encoding="utf-8")
    output(result.text)
    output("")
    output(t("log: {log_path}", log_path=log_path))
    return result.exit_code


def apply(
    *,
    vault: Path,
    runner: Runner,
    state_dir: Path,
    timestamp: str,
    engine_scripts: Path | None,
    push_if_clean: bool,
    log_path: Path | None = None,
    input_func: Callable[[str], str] = input,
    output: Callable[[str], object] = print,
) -> int:
    """Proposes, shows, confirms, and only then executes -- inside the clone."""
    propose_log = log_path or make_log_path(".propose")
    prompt = prompts.build_propose_prompt(PLAYBOOK, str(vault))
    proposal = runner.run_readonly(prompt)
    propose_log.write_text(proposal.text, encoding="utf-8")
    tranche = proposal.text

    if not tranche.strip():
        output(t("vault-groom: empty proposal, nothing to review -- aborting."))
        return 1

    plan_record = gate.write_plan_record(state_dir, timestamp, tranche)
    tranche_hash = gate.hash_plan_record(plan_record)

    if not gate.confirm_tranche(tranche, tranche_hash, input_func=input_func, output=output):
        output(t("vault-groom: cancelled, no changes to the vault."))
        return 0

    try:
        gate.verify_hash_unchanged(plan_record, tranche_hash)
        gate.require_clean_tree(vault)
    except gate.GateError as exc:
        output(str(exc))
        return 1

    base = git(vault, "rev-parse", "HEAD").strip()
    branch = git(vault, "rev-parse", "--abbrev-ref", "HEAD").strip()

    try:
        clone_dir = gate.prepare_clone(vault, state_dir, timestamp)
    except (subprocess.CalledProcessError, GitCommandError) as exc:
        output(t("vault-groom: could not prepare the temp clone: {error}", error=exc))
        return 1

    archive_root = resolve_archive_root(vault)
    write_prompt = prompts.build_write_prompt(PLAYBOOK, str(plan_record), tranche_hash, tranche)
    write_log = make_log_path(".execute")
    write_result = runner.run_write(write_prompt, clone_dir)
    write_log.write_text(write_result.text, encoding="utf-8")

    request = audit.AuditRequest(
        vault=vault,
        clone=clone_dir,
        branch=branch,
        base=base,
        archive_root=archive_root,
        state_dir=state_dir,
        timestamp=timestamp,
        runner=runner.name,
        model=runner.model,
        tranche_sha256=tranche_hash,
        plan_record=plan_record,
        propose_log=propose_log,
        write_log=write_log,
        write_exit_code=write_result.exit_code,
        push_if_clean=push_if_clean,
        engine_scripts=engine_scripts,
    )
    record, exit_code = audit.run_audit(request, output=output)

    state_dir.mkdir(parents=True, exist_ok=True)
    record_path = state_dir / f"{timestamp}.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    output(t("audit record: {record_path}", record_path=record_path))

    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = args[0] if args else "preview"

    if mode in ("-h", "--help"):
        print(usage())
        return 0
    if mode in RETIRED_MODES:
        print(RETIRED_MODES[mode], file=sys.stderr)
        return 2
    if mode not in ("preview", "apply"):
        print(t("vault-groom: unknown mode '{mode}'\n\n{usage}", mode=mode, usage=usage()), file=sys.stderr)
        return 2

    vault = resolve_vault_data()
    print(t("vault-groom: vault = {vault}", vault=vault), file=sys.stderr)
    if not vault.is_dir():
        print(t("vault-groom: the vault does not exist or is not a folder: {vault}", vault=vault), file=sys.stderr)
        return 3

    runner_name = os.environ.get("GROOM_RUNNER", DEFAULT_RUNNER)
    model = os.environ.get("GROOM_MODEL", DEFAULT_MODEL)
    state_dir = Path(os.environ.get("GROOM_STATE_DIR") or resolve_home() / ".local" / "state" / "vault-groom")
    log_override = os.environ.get("GROOM_LOG")
    log_path = Path(log_override) if log_override else None

    try:
        active_runner = get_runner(runner_name, model, vault)
    except RunnerError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if mode == "preview":
        return preview(vault=vault, runner=active_runner, log_path=log_path)

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    engine_scripts = resolve_engine_root() / "scripts"
    push_if_clean = os.environ.get("GROOM_NOPUSH", "0") != "1"
    return apply(
        vault=vault,
        runner=active_runner,
        state_dir=state_dir,
        timestamp=timestamp,
        engine_scripts=engine_scripts,
        push_if_clean=push_if_clean,
        log_path=log_path,
    )

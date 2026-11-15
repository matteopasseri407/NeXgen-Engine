"""Whether each CLI really calls the guardrail that is registered for it, whether the CLI that
asks a person first has been answered, and what the private permission manifest no longer decides."""
from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Severity
from nexgen_core.runtimes import REGISTRY


def _read_record(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def check_guardrail_consulted(home: Path) -> list[CheckOutcome]:
    """One outcome per CLI that has a guardrail installed.

    A registration only proves the hook is written down. Whether the CLI ever reaches it is
    another question, and the one that matters: OpenCode's hook is only called for actions it
    is about to ask about, so a posture that allowed shell outright left the plugin registered
    and unreachable behind a green check. Each adapter records every consultation (see
    nexgen-guardrail-core.mjs), so "consulted at least once since install" is a fact the doctor
    can read. Never consulted is undetermined, not broken: it is also what a CLI nobody has used
    since the install looks like.
    """
    outcomes: list[CheckOutcome] = []
    for runtime in REGISTRY.values():
        if not runtime.is_installed(home):
            continue
        sidecar_path = runtime.guardrail_sidecar(home)
        if sidecar_path is None or not sidecar_path.is_file():
            continue
        sidecar = runtime.read_guardrail_sidecar(sidecar_path)
        if not sidecar.get("hooks"):
            continue
        audit = Path(sidecar.get("auditFile") or runtime.guardrail_audit_file(home, runtime.name))
        record = _read_record(audit)
        check_id = f"guardrail.consulted.{runtime.name}"
        if not record.get("count"):
            outcomes.append(CheckOutcome(
                id=check_id,
                severity=Severity.UNDETERMINED,
                message=t(
                    "{cli}: the guardrail is installed but has never been consulted. Either nothing has been "
                    "run in {cli} since it was installed, or {cli} is not calling it.",
                    cli=runtime.name,
                ),
                action=t(
                    "Run a shell command in {cli} and check again. If this stays, {cli} is not reaching the "
                    "guardrail and its shell commands are not being checked.",
                    cli=runtime.name,
                ),
            ))
            continue
        minutes = max(0.0, time.time() - float(record.get("at", 0)) / 1000) / 60
        outcomes.append(CheckOutcome(
            id=check_id,
            severity=Severity.OK,
            message=t(
                "{cli}: the guardrail was consulted {count} times, last {minutes:.0f} minutes ago.",
                cli=runtime.name, count=int(record["count"]), minutes=minutes,
            ),
        ))
    return outcomes


def check_permission_posture(vault_data: Path) -> CheckOutcome | None:
    """A leftover `posture:` block in the private permission manifest, said once.

    The engine used to translate that block into each app's own mode keys on every cycle,
    which reverted a choice made in the app's settings screen. It reads nothing from the key
    now: each app's own setting decides. A block left in the manifest is not an error and is
    never rewritten -- removing it is the owner's call -- but the doctor says what it no longer
    does, so a manifest that still declares `bypass` is not read as a machine that bypasses
    prompts. OpenClaw is the exception the line names: it has no app-side approval setting of
    its own, so its plugin reads `posture` from the engine-owned `nexgen.config.json` and
    behaves as `bypass` when the key is absent.
    """
    manifest = vault_data / "03-INFRA" / "agent-universal-layer" / "permissions" / "manifest.yaml"
    if not manifest.is_file():
        return None
    try:
        raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):  # nexgen: allow reason="an unreadable manifest is already reported by the permissions phase and its own warnings; this line only says what is not applied"
        return None
    if not isinstance(raw, dict) or "posture" not in raw:
        return None
    return CheckOutcome(
        id="permissions.posture",
        severity=Severity.OK,
        message=t(
            "posture is no longer applied: each app's own setting decides, except OpenClaw, whose "
            "plugin reads 'posture' in its own nexgen.config.json and behaves as bypass without it"
        ),
    )


def check_codex_guardrail_trust(home: Path) -> CheckOutcome | None:
    """Whether Codex has accepted the guardrail hook it will not run without a person's yes.

    Codex records trust as a hash of the hook's own definition and skips a new or changed hook
    until somebody accepts it in `/hooks` (developers.openai.com/codex/hooks, "Review and trust
    hooks"). The engine never writes that hash, because that would be consent nobody gave: a
    hook written and never accepted looks exactly like a working guardrail, and this line is
    what makes the difference visible without faking the decision.
    """
    from nexgen_core.runtimes.base import GuardrailError
    from nexgen_core.runtimes.codex import STALE, TRUSTED, CodexRuntime, guardrail_trust

    runtime = CodexRuntime()
    if not runtime.is_installed(home):
        return None
    sidecar = runtime.guardrail_sidecar(home)
    if sidecar is None or not sidecar.is_file():
        return None
    try:
        state = guardrail_trust(home)
    except GuardrailError as exc:
        return CheckOutcome(
            id="guardrail.codex_trust",
            severity=Severity.WARN,
            message=t("Codex: its own hook file could not be read, so the guardrail's trust is unknown ({error}).", error=exc),
            action=t("Fix the JSON in Codex's hooks.json, then run 'nexgen doctor' again."),
        )
    if state is None:
        return CheckOutcome(
            id="guardrail.codex_trust",
            severity=Severity.WARN,
            message=t("Codex: the guardrail is deployed but not registered in its own hooks.json."),
            action=t("Run 'nexgen sync' to register it. Until then Codex's shell commands are not checked."),
        )
    if state == TRUSTED:
        return CheckOutcome(
            id="guardrail.codex_trust",
            severity=Severity.OK,
            message=t("Codex has accepted the guardrail hook, so Codex runs it."),
        )
    if state == STALE:
        return CheckOutcome(
            id="guardrail.codex_trust",
            severity=Severity.WARN,
            message=t(
                "Codex: the guardrail hook changed after it was accepted, so Codex skips it until it is reviewed again."
            ),
            action=t("Open Codex, run /hooks and accept the new version of the NeXgen guardrail hook."),
        )
    return CheckOutcome(
        id="guardrail.codex_trust",
        severity=Severity.WARN,
        message=t("Codex: the guardrail hook is registered but not trusted, so Codex skips it."),
        action=t("Open Codex, run /hooks and accept the NeXgen guardrail hook."),
    )


def check_openclaw_guardrail_plugin(home: Path) -> CheckOutcome | None:
    """OpenClaw's guardrail: is the plugin deposited, and is it enabled?

    Two separate facts, because they are repaired by two different steps. The
    plugin directory is deposited by the guard cycle (byte for byte, with its
    sidecar); enabling it is a `plugins.*` change, which the guard never makes
    because editing that block under a running gateway breaks it.
    """
    from nexgen_core.mcp_render import load_json_object
    from nexgen_core.paths import openclaw_config
    from nexgen_core.runtimes.openclaw import PLUGIN_ID, OpenClawRuntime

    runtime = OpenClawRuntime()
    if not runtime.is_installed(home):
        return None
    sidecar = runtime.guardrail_sidecar(home)
    if sidecar is None:  # never for this adapter; the contract allows it
        return None
    plugin_dir = sidecar.parent
    if not sidecar.is_file() or not (plugin_dir / "index.mjs").is_file():
        return CheckOutcome(
            id="guardrail.openclaw_plugin",
            severity=Severity.WARN,
            message=t("OpenClaw's guardrail plugin is not deposited in {path}.", path=plugin_dir),
            action=t(
                "Run 'nexgen sync' to deposit it; enabling it is a separate step, 'nexgen openclaw setup'."
            ),
        )
    try:
        config = load_json_object(openclaw_config(home))
    except ValueError as exc:
        return CheckOutcome(
            id="guardrail.openclaw_plugin",
            severity=Severity.UNDETERMINED,
            message=t("OpenClaw's configuration could not be read, so the plugin's state is unknown: {error}", error=exc),
            action=t("Fix the JSON in OpenClaw's own configuration file, then run 'nexgen doctor' again."),
        )
    reason = openclaw_plugin_disabled_reason(config, PLUGIN_ID)
    if reason:
        return CheckOutcome(
            id="guardrail.openclaw_plugin",
            severity=Severity.WARN,
            message=t("OpenClaw's guardrail plugin is deposited but not enabled ({reason}).", reason=reason),
            action=t(
                "Run 'nexgen openclaw setup' to enable it with the gateway stopped: a plugins.* change "
                "on a running gateway breaks it. Until then OpenClaw's shell commands are not checked."
            ),
        )
    return CheckOutcome(
        id="guardrail.openclaw_plugin",
        severity=Severity.OK,
        message=t("OpenClaw's guardrail plugin is deposited and enabled"),
    )


def openclaw_plugin_disabled_reason(config: dict, plugin_id: str) -> str | None:
    """Why OpenClaw would not load `plugin_id`, or None when it would.

    The three gates are the ones OpenClaw documents: an explicit `deny`, an
    entry switched off, and a non-empty `allow` list that does not name it. An
    absent or empty `allow` list is not "disabled": it means no allowlist at all.
    """
    plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    deny = plugins.get("deny")
    if isinstance(deny, list) and plugin_id in {str(name).lower() for name in deny}:
        return t("it is in plugins.deny")
    entries = plugins.get("entries") if isinstance(plugins.get("entries"), dict) else {}
    entry = entries.get(plugin_id) if isinstance(entries.get(plugin_id), dict) else {}
    if entry.get("enabled") is False:
        return t("plugins.entries.{id}.enabled is false", id=plugin_id)
    allow = plugins.get("allow")
    if isinstance(allow, list) and allow and plugin_id not in {str(name).lower() for name in allow}:
        return t("plugins.allow names other plugins only")
    return None

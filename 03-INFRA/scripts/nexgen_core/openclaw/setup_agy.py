"""The Antigravity plugin step of `nexgen openclaw setup`.

One job: when the team runs an agent on the local `agy` CLI, put the pinned
`google-antigravity-cli` plugin on this gateway before the configuration that
enables it is written, and record the agent folders `agy` must trust so no agent
stops on its prompt.

The order is not cosmetic. A configuration that enables a plugin which is not
there is a gateway that does not start, so the install comes first and a failed
install stops the setup with nothing written. A machine whose team names no
`agy` model does nothing here and says nothing: the same bytes as before.

Nothing in this module installs the `agy` CLI or signs in for the person. Those
two are theirs, and the step only names the command.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text, backup_file
from nexgen_core.i18n import t
from nexgen_core.openclaw import agy_prereqs
from nexgen_core.openclaw.setup_steps import Setup, openclaw_bin, print_output
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND, local_agy_backend
from nexgen_core.processes import command_line_text

#: The tag a copy of `agy`'s settings carries, so the file it protects stays recognisable.
TRUST_BACKUP_TAG = "agy-trust"


def install_step(setup: Setup, config: dict[str, Any]) -> bool:
    """The pinned plugin, when the configuration about to be written drives `agy`.

    `config` is the proposal, not the file in force: an agent the squad adds is
    in it, and a backend the person approved earlier is kept there by the plan.
    """
    if local_agy_backend(config) is None:
        return True
    plugin = agy_prereqs.plugin_pin(setup.engine_root)
    if plugin is None:
        setup.out(t("The engine cannot read its Antigravity plugin pin, so it will not install an unpinned "
                    "plugin. Check agent-universal-layer/openclaw/agy-plugin.json and run the setup again."))
        return False
    installed = agy_prereqs.installed_version(setup.state_dir)
    if installed == plugin["version"]:
        setup.out(t("The Antigravity plugin {plugin} is already {version}.",
                    plugin=LOCAL_AGY_BACKEND, version=plugin["version"]))
        _prerequisites(setup, config)
        return True
    return _install(setup, config, plugin, installed)


def _install(setup: Setup, config: dict[str, Any], plugin: dict[str, str], installed: str | None) -> bool:
    """Run the one install command, then prove the release on disk is the pinned one."""
    argv = agy_prereqs.install_argv(openclaw_bin(setup), plugin, replace=installed is not None)
    if installed:
        setup.out(t("The Antigravity plugin {plugin} is {version}, not the pinned {pinned}: installing the pin.",
                    plugin=LOCAL_AGY_BACKEND, version=installed, pinned=plugin["version"]))
    else:
        setup.out(t("An agent runs on the local 'agy' CLI and the plugin is not installed: installing {pinned}.",
                    pinned=plugin["version"]))
    setup.out("$ " + command_line_text(argv))
    if setup.dry_run:
        return True
    result = setup.run(argv, setup.env)
    if result.returncode != 0:
        setup.out(t("The Antigravity plugin install failed (exit {code}).", code=result.returncode))
        print_output(setup.out, result)
        return False
    found = agy_prereqs.installed_version(setup.state_dir)
    if found != plugin["version"]:
        setup.out(t("The install finished but the plugin is {version} in {path}, not {pinned}: the "
                    "configuration was not written.",
                    version=found or "?", path=agy_prereqs.extension_dir(setup.state_dir),
                    pinned=plugin["version"]))
        return False
    setup.out(t("The Antigravity plugin {plugin} {version} is installed.",
                plugin=LOCAL_AGY_BACKEND, version=plugin["version"]))
    _prerequisites(setup, config)
    return True


def _prerequisites(setup: Setup, config: dict[str, Any]) -> None:
    """What `agy` itself needs, in the one step that fixes each thing."""
    if setup.which("agy") is None:
        setup.out(t("The 'agy' command is not on PATH: install the Antigravity CLI from Google, then run "
                    "'agy' once and sign in. The plugin cannot run an agent until then."))
        return
    if agy_prereqs.login_file(setup.home) is None:
        setup.out(t("The login of 'agy' is kept by the system keyring and cannot be checked from here: run "
                    "'agy' once and make sure it answers."))
    missing = _missing_folders(setup, config)
    if missing:
        _trust_folders(setup, missing)


def _missing_folders(setup: Setup, config: dict[str, Any]) -> list[str]:
    """The agent folders `agy` would stop on, resolved the way OpenClaw resolves them."""
    return agy_prereqs.untrusted(
        agy_prereqs.agent_workspaces(config, setup.state_dir, setup.home),
        agy_prereqs.trusted_workspaces(setup.home),
    )


def _trust_folders(setup: Setup, folders: list[str]) -> None:
    """Record the folders in `agy`'s own trust list, beside a copy of the old file.

    A file this engine cannot read is never replaced, and the manual step is
    printed instead: the settings of another program hold keys nothing here
    inspects, and the file holds an answer the person already gave. A dry run
    prints the plan and touches nothing.
    """
    path = agy_prereqs.settings_path(setup.home)
    document, trusted = _trust_document(path)
    if document is None:
        _say_manually(setup, folders)
        return
    if setup.dry_run:
        setup.out(_trust_plan(setup, folders))
        return
    try:
        backup = backup_file(path, tag=TRUST_BACKUP_TAG)
        document[agy_prereqs.TRUST_KEY] = [*trusted, *folders]
        atomic_write_text(path, json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    except OSError as exc:  # nexgen: allow reason="a settings file that cannot be written is reported with the manual step, not a crash after a successful plugin install"
        setup.out(t("The trust list at {settings} could not be written ({error}).", settings=path, error=exc))
        _say_manually(setup, folders)
        return
    if backup is None:
        setup.out(t("'agy' is now trusted with {count} more agent folders; {settings} was created.",
                    count=len(folders), settings=path))
        return
    setup.out(t("'agy' is now trusted with {count} more agent folders; the previous settings are saved as "
                "{backup}.", count=len(folders), backup=backup))


def _trust_document(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """`agy`'s settings and its current trust list, or None when they cannot be trusted.

    A missing file is an empty document: `agy` writes it on its first trust
    answer, and the setup is that answer here. A file that is not a JSON object,
    or whose trust list is not a list of paths, is left untouched, because
    rewriting a value this engine does not understand would silently edit
    another program's settings.
    """
    if not path.exists():
        return {}, []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable settings file is left as it is and the manual step is printed"
        return None, []
    if not isinstance(loaded, dict):
        return None, []
    values = loaded.get(agy_prereqs.TRUST_KEY, [])
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        return None, []
    return loaded, list(values)


def _say_manually(setup: Setup, folders: list[str]) -> None:
    """The two lines this step printed before it could write the file itself."""
    setup.out(t("'agy' has not been trusted with these agent folders: {folders}", folders=", ".join(folders)))
    setup.out(t("Run 'agy' once in each of them and accept the trust prompt; 'agy' records the folder in "
                "{settings} under {key}.",
                settings=agy_prereqs.settings_path(setup.home), key=agy_prereqs.TRUST_KEY))


def _trust_plan(setup: Setup, folders: list[str]) -> str:
    """What a real run would add to `agy`'s settings, for `--dry-run` to print instead."""
    return t("dry run: 'agy' would be trusted with {count} more agent folders in {settings}; nothing is written.",
             count=len(folders), settings=agy_prereqs.settings_path(setup.home))


def planned_lines(setup: Setup, config: dict[str, Any]) -> list[str]:
    """The install command and the trust write a real run would do, for `--dry-run`."""
    if local_agy_backend(config) is None:
        return []
    plugin = agy_prereqs.plugin_pin(setup.engine_root)
    if plugin is None:
        return ["# " + t("the Antigravity plugin pin cannot be read: no plugin is installed")]
    lines: list[str] = []
    installed = agy_prereqs.installed_version(setup.state_dir)
    if installed != plugin["version"]:
        lines.append(command_line_text(
            agy_prereqs.install_argv(openclaw_bin(setup), plugin, replace=installed is not None),
        ))
    if setup.which("agy") is not None:
        missing = _missing_folders(setup, config)
        if missing:
            lines.append(_trust_plan(setup, missing))
    return lines

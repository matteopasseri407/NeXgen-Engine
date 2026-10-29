"""The Antigravity plugin step of `nexgen openclaw setup`.

One job: when the team runs an agent on the local `agy` CLI, put the pinned
`google-antigravity-cli` plugin on this gateway before the configuration that
enables it is written, and say what `agy` itself still needs.

The order is not cosmetic. A configuration that enables a plugin which is not
there is a gateway that does not start, so the install comes first and a failed
install stops the setup with nothing written. A machine whose team names no
`agy` model does nothing here and says nothing: the same bytes as before.

Nothing in this module installs the `agy` CLI or signs in for the person. Those
two are theirs, and the step only names the command or the folder.
"""
from __future__ import annotations

from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw import agy_prereqs
from nexgen_core.openclaw.setup_steps import Setup, openclaw_bin, print_output
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND, local_agy_backend
from nexgen_core.processes import command_line_text


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
    argv = agy_prereqs.install_argv(openclaw_bin(setup), plugin)
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
    missing = agy_prereqs.untrusted(
        agy_prereqs.agent_workspaces(config), agy_prereqs.trusted_workspaces(setup.home),
    )
    if not missing:
        return
    setup.out(t("'agy' has not been trusted with these agent folders: {folders}", folders=", ".join(missing)))
    setup.out(t("Run 'agy' once in each of them and accept the trust prompt; 'agy' records the folder in "
                "{settings} under {key}.",
                settings=agy_prereqs.settings_path(setup.home), key=agy_prereqs.TRUST_KEY))


def planned_lines(setup: Setup, config: dict[str, Any]) -> list[str]:
    """The install command a real run would execute, for `--dry-run` to print instead."""
    if local_agy_backend(config) is None:
        return []
    plugin = agy_prereqs.plugin_pin(setup.engine_root)
    if plugin is None:
        return ["# " + t("the Antigravity plugin pin cannot be read: no plugin is installed")]
    if agy_prereqs.installed_version(setup.state_dir) == plugin["version"]:
        return []
    return [command_line_text(agy_prereqs.install_argv(openclaw_bin(setup), plugin))]

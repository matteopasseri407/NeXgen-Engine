# What is installed, and keeping it current

🇮🇹 [Italiano](it/whats-installed.md)

`nexgen info` lists the MCP servers and the skills on this machine: who owns each one, what it is pinned to, and
whether upstream has moved. It reads local files only. The upstream answer is whatever the hourly dependency watch
last left behind, so `info` returns at once and never touches the network.

## Who owns an item

One word per server and per skill.

| Word | Meaning | What updates it |
| --- | --- | --- |
| `core` | Shipped with the engine. | `nexgen update`: the engine carries it. |
| `yours` | Lives in your Vault or on your disk. | You. Nothing else touches it. |
| `third-party` | Someone else's package, repository or service. | `nexgen mcp bump` / `nexgen skills bump`, after the guardian has looked at it. |

A server is judged by how it starts. A package launcher (`npx`, `uvx`, `docker`, ...) is third-party whatever it is
handed as arguments; a path under `${AGENT_ENGINE_ROOT}` or a `nexgen-*` command is core; a path in your Vault or an
absolute path on your disk is yours. A skill is judged by its `origin`: `engine` is core, `vault` is yours, and
`github`, `installer` and `upstream` are third-party. One case overrides that: a skill the engine also ships, kept in
the Vault with `origin: vault`, counts as core, and `info` says whether the Vault copy still matches the engine's.

## What each line says

For a server: where it is mounted (`direct`, `lazy` for behind the gateway, `mixed`, `off`, `in prova`/`trial`, with
the CLIs it is limited to in brackets), the version it is pinned to, and notes underneath:

- `upstream X available (pinned Y)`, plus whether the guardian cleared the change or holds it, with the reason in
  plain words;
- `not pinned`: an `npx` server with no `package@version` runs whatever the registry serves today, and the dependency
  watch cannot see it. Pin it in the manifest.
- `N tools hidden`: the entry's `tools_deny` (see [lazy-mcp.md](lazy-mcp.md)).
- why a server is mounted nowhere.

For a skill: its origin and pin, whether upstream moved, whether it is declared but not installed here, and, for a
core skill with a Vault copy, whether the copy differs from the engine's. The copy is what runs, so it does not
follow the engine's updates: identical today, stale after the next release. `nexgen skills adopt` hands it back
(below).

Skills that are simply yours appear as a count; `nexgen info --all` lists them. `nexgen info --json` carries the same
data under `extensions`.

## Updating

`nexgen mcp bump` and `nexgen skills bump` are the same command. It shows every update the guardian cleared, in plain
words, and asks once. Updates that need a human look stay held and are never touched.

For MCP servers it does one thing more before keeping the change: it starts each server whose pin moved, exactly as
the CLIs would, and checks that it lists tools. If one does not work, every pin goes back where it was and the CLIs'
configurations are regenerated from the old ones; nothing is committed. Behind the gateway, a server that is only
missing a credential in this session does not count against the update.

## What updates by itself, what waits for you

Every third-party item follows one of five paths, and `nexgen thirdparty status` prints, one line per item, which one
applies to it, the version it is pinned to, the newest one upstream, when that was last checked, and the exact
command that moves it. Ask that command before asking anything else.

| Policy it prints | What it means | How it moves |
| --- | --- | --- |
| `moves by itself` | The bytes are identical between the old pin and the new one, so nothing can change. | The hourly watch raises it and commits. Nothing to do. |
| `waits for your yes` | Prose only, a descendant commit, or an npm patch: safe-shaped and small. | `nexgen skills bump`, one yes for all of them. |
| `needs a look first` | A new script, a hook touched, a minor or major version, a rewritten history, or a version its publisher withdrew. | **No command raises it.** Read what changed, then edit the pin in the file the line names. |
| `by hand, where it runs` | A component inside a module: the n8n image, the Playwright launcher, OpenClaw itself. | Replace it where it runs, after trying it. The engine never rewrites these. |
| `floating, no pin` | An `npx` server with no `package@version`, or an endpoint somebody else serves (`mcp.vercel.com`). | It already changes without anyone deciding. Served by its owner, or pin a version to take that back. |

Three things that surprise people:

- **Nothing updates on `nexgen sync`.** The sync materializes the pin written in the manifest. A pinned skill or
  server is byte-identical before and after a sync, whatever upstream published in the meantime.
- **A newer version is not an approved update.** The dependency watch only says that something moved; the guardian
  decides whether the move is boring. `nexgen info` counts what moved, so its number can be larger than the number of
  things waiting for you.
- **`nexgen skills bump` doing nothing is an answer, not a failure.** It prints how many items stay held, and
  `nexgen thirdparty status` says what each of them needs.

`nexgen doctor` carries one line for all of it: how many updates are cleared and waiting for a yes (with the command
that applies them), or how many need a look. It is never red because of a pending update, and `doctor -v` prints it.

### Where those mechanics come from

The engine decides what governs an item and keeps the manifest; the mechanics of a given source are increasingly
somebody else's, and that is deliberate: a maintained project that owns installing one kind of thing will notice a
change upstream before this engine does.

- **A github skill** is fetched by the [`skills` CLI](https://github.com/vercel-labs/skills) (MIT, pinned to
  npm `skills@1.8.1`) when Node is installed: it resolves the commit in the manifest and downloads that one skill
  folder instead of the whole repository, and the folder it leaves is the one the engine copies into the library. The
  integrity check is the tool's own `skills-lock.json`, which must name the pinned commit and a content hash, or the
  bytes are refused. A machine without Node clones the repository at the pin, exactly as before; a tool that is absent,
  fails or answers with another commit costs nothing, because the clone follows. The tool's `update` is not used: it
  moves its own installs and its own agent directories, so it would be a second writer of the same bytes. The mover
  stays `nexgen skills bump`.
- **A plugin** belongs to the harness that installed it, and the engine reports it instead of copying the update:
  `nexgen thirdparty status` ends with the plugins Claude Code, Codex and Antigravity report through their own
  `plugin list --json`, each with the command that moves it (`claude plugin update <plugin>`,
  `codex plugin marketplace upgrade`, `agy plugin install <plugin>`). Nothing there is ever run by the engine.
- **The version of an MCP server** is read from npm and from nowhere else. The official MCP registry was measured
  against it and refused: it trails npm (one publisher's server was fourteen releases behind), and two of the servers
  this machine pins are not listed under their package name at all, so reading it would have hidden the releases the
  watch exists to report.

## Skills the engine ships

Older installs copied the engine's starter skills into the Vault and declared the copies `origin: vault`. A copy is
frozen the day it is made: the next release fixes the skill, the copy keeps the old text, and every CLI runs the copy.
`origin: engine` links the engine's own folder instead, so `nexgen update` is the only step.

`nexgen skills adopt` lists the copies and says which are identical to the engine's and which differ.
`nexgen skills adopt --all` switches the identical ones: it changes the entry's `origin` line (nothing else in the
manifest, comments included), moves the Vault's copy to a backup folder under the machine's state directory, and
re-links. A copy that differs is left alone, because it may hold something you wrote; look at what differs, then name
it with `--force` if the engine's text is the one you want. `nexgen doctor` warns while any such copy exists, and
`doctor --fix` adopts the identical ones. Publish the result with `nexgen vault push` so the other machines follow.

A skill you want to keep your own way stays `origin: vault` and is simply yours: nothing touches it, and it stops
being flagged once it no longer shares a name with a skill the engine ships. Rename it to say so.

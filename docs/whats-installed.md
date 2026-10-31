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

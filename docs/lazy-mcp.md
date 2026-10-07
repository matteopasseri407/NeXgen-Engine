# The lazy MCP proxy (`lazy-mcp`)

Servers the manifest marks `lazy: true` are not mounted in your CLIs. One small always-on
server, `lazy-mcp`, stands in front of them: it lists their tools, loads one tool's full
definition when the model asks, and forwards the call. The CLI pays for four meta-tools
instead of the schemas of every server.

## Which CLI gets what

Where a server lives is one rule (`nexgen_core/mcp_placement.py`), asked by the renderer, by the gateway and
by `nexgen mcp plan` alike. A server is, per CLI, mounted **directly**, served by the **gateway**, or **absent**.
Declare it once:

```yaml
servers:
  vault-library: { exposure: eager, ... }   # mounted directly in every CLI listed in targets
  github:        { exposure: lazy,  ... }   # behind the gateway in every CLI listed in targets
```

`targets` is always the allow-list of CLIs and `enabled: false` always switches a server off. A manifest that
does not declare `exposure` keeps working through the older knobs (`tier`, `lazy`, `lazy_targets`). Each CLI's
copy of the gateway is told which CLI it is in (`LAZY_MCP_CLI`, written by the renderer) and serves exactly
what the plan routes behind it there: a server mounted directly in a CLI is not also in that CLI's gateway, and
one restricted to two CLIs is not offered by the other two. A gateway that was never told its CLI (a config
written before this) serves every lazy server until the next guard cycle rewrites it.

```bash
nexgen mcp plan     # the table: server x CLI, direct / gateway / -, and why; what changed for the gateway; what is incoherent
nexgen mcp check    # starts each CLI's gateway as that CLI would, lists what it serves, names any backend that cannot start
```

`nexgen doctor` reports an incoherent plan statically (`mcp.placement`); `check` is the live half and starts the
backends, so it is a command you run, not part of `doctor`.

What stops the gateway's children: a CLI that goes away closes the gateway's input, and the gateway and its
backends exit with it (tested). A gateway killed outright (the OOM killer, `kill -9`) cannot clean up after
itself and leaves its backends running until they are stopped by hand.

## The four tools

| Tool | What it does |
| --- | --- |
| `lazy_list` | The index: each server, its tool names, one-line hints. If a server could not be started, its entry carries `error` with why. |
| `lazy_load` | One tool's full definition. Always before calling it. |
| `lazy_call` | Forwards a call to a tool the manifest declares read-only (`readonly: true` on the server, or `readonly_tools: [...]`). Refuses anything else. |
| `lazy_mutate` | Forwards a call to any other tool. Needs `"confirm": true`. |

Reads and writes are separate tools so that your CLI's own permission can tell them apart:
allow `lazy_call` and still be asked about `lazy_mutate`. Read-only is **never** inferred from a
tool's name or from the server's own annotations (they come from a server you do not control):
the manifest has to say it. `"confirm": true` is the model acknowledging that the call changes
state; it is not your approval. Your approval is your CLI's prompt for `lazy_mutate`, which a
bypass posture removes by design, so under bypass the audit log is the only record.

Every load, call and refusal is appended to `~/.local/state/lazy-mcp-audit.jsonl` (under
`NEXGEN_HOME` or `XDG_STATE_HOME` when set).

## What a server is given

A stdio server starts with a **filtered** environment: what a runtime needs to start (`PATH`,
`HOME`, locale, temp dirs, proxy and certificate settings, `XDG_*`, `NODE_*`, `NPM_CONFIG_*`,
`PYTHON*`, the engine's own `NEXGEN_*`/`AGENT_*`), minus anything whose name looks like a secret
(`*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*API_KEY*`, `*_KEY`, and so on). The proxy's environment holds
every token your shell exports, and a server used to get all of them.

A server that needs one says so in its manifest entry, and only then does it receive it:

```yaml
servers:
  my-server:
    lazy: true
    command: npx
    args: ["-y", "my-mcp@1.2.3"]
    env:
      MY_SERVICE_TOKEN: "${MY_SERVICE_TOKEN}"
```

## When a server does not start

A server that dies on startup (a missing import, a bad path) used to be reported as
"tool not found", with whatever it printed thrown away. The last lines of its stderr are now kept
and shown in the error, with the values of the secrets the proxy gave it and the usual token shapes
replaced by `[redacted]`.

## Concurrency

Tool calls are served concurrently: a slow call to one server no longer holds up a `ping` or a
call to another. One server's own calls stay serial (its pipe is a single stream). At most 32
requests are in flight (`LAZY_MCP_MAX_CONCURRENCY`).

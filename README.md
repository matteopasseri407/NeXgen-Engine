# NeXgen Engine

<p align="center">
  <picture>
    <source srcset="assets/nexgen-architecture-banner.webp" type="image/webp">
    <img src="assets/nexgen-architecture-banner.png" alt="NeXgen Engine, AI operating layer" width="100%" loading="eager">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/matteopasseri407/NeXgen-Engine/actions/workflows/ci.yml"><img src="https://github.com/matteopasseri407/NeXgen-Engine/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/matteopasseri407/NeXgen-Engine/releases/latest"><img src="https://img.shields.io/github/v/release/matteopasseri407/NeXgen-Engine?display_name=tag&label=latest%20version" alt="Latest version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-00E5B8?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/platform-linux%20%7C%20windows-lightgrey" alt="Linux and Windows">
</p>

<p align="center">
  <a href="README.it.md">🇮🇹 Italiano</a> · <a href="#quick-start">Quick start</a> · <a href="#how-it-compares">How it compares</a> · <a href="docs/architecture-contract.md">Architecture</a> · <a href="CHANGELOG.md">Changelog</a>
</p>

**Make the AI subscriptions you already pay for work as one team, across all your machines.**

NeXgen Engine is one Git repository that configures Claude Code, Codex, OpenCode, Antigravity and OpenClaw from a single source, and keeps them aligned on every PC you use.

## What you get

- More out of the flat plans. `nexgen quota` reads what the harnesses on this machine already wrote about their own usage: what each plan has used, the percentage where the provider publishes one, the reset time when it is known, and how old the reading is. A window it cannot measure from here says so instead of showing a guess. If you declare a fallback chain in `quota.yaml`, `nexgen openclaw setup` writes it into the OpenClaw agents the engine creates: once the last reading is past the line, 95% by default, the next turn starts on the next model instead of running into a spent plan. See [quota.md](docs/quota.md).

- Models from different vendors on the same job. `council` convenes the CLIs you already have as seats, each on its own model, to brainstorm, attack a plan or review code written by another vendor. It is opt-in: with no `seats.yaml` there is no Council. See [council.md](docs/council.md). A supervised task goes further: a frontier model writes the brief, an executor carries it out in a window of its own harness, and the engine keeps the files, the cost register and the gates that run before anyone reviews a diff. That part is a preview, and the docs say what the gates do not guarantee. See [supervision.md](docs/supervision.md).

- Connectors and skills configured once. One MCP manifest and one skills manifest in the Vault are rendered into each CLI's own native format by `nexgen sync`. The merge is additive, so a server you added by hand survives. Claude Desktop is the exception: it gets only the connectors you opt into with `desktop_allow: true`, and only for read-only servers. See [sync-contract.md](docs/sync-contract.md) and [what-gets-written.md](docs/what-gets-written.md).

- Several PCs from one source. `nexgen sync` applies the configuration on each machine and `nexgen doctor` reports what drifted. Secrets live in one age-encrypted file with a key per enrolled machine, and OAuth refresh tokens are stored per host, so a token that rotates on one PC does not break another. See [roles.md](docs/roles.md).

- Memory you can read and diff. The KnowledgeVault is Markdown in Git: notes, decisions and project state. Writes go through the Vault MCP, which checks the note or section hash first and refuses a stale replacement. See [architecture-contract.md](docs/architecture-contract.md).

- One harness for everything, if you want it. OpenClaw is the engine's official harness: a web panel and a phone app over the subscriptions you already pay for, an agent squad declared in `squad.yaml`, one node per PC and channel bindings that route a chat to the right agent. See [openclaw.md](docs/openclaw.md) and [squad.md](docs/squad.md).

## Demo

```bash
nexgen info    # dashboard: engine version, runtimes, modules, installed MCP servers and skills, secrets
nexgen doctor  # checks: git alignment, MCP reachability, modules and skills, secrets and their file modes
```

<p align="center">
  <picture>
    <source srcset="assets/nexgen-info-demo.webp" type="image/webp">
    <img src="assets/nexgen-info-demo.png" alt="nexgen info dashboard on Windows: Host, Vault, Planes & Runtimes, Modules and Security & Diagnostics at a glance" width="100%">
  </picture>
  <br><em><code>nexgen info</code> on Windows, showing host, Vault, runtimes, modules, and diagnostics. Run <code>nexgen doctor</code> for the full report.</em>
</p>

## How it works

The engine keeps three things apart, each with one canonical source in the Vault:

1. Behavior: operating rules, prompts and guardrails in `AGENTS.md`, linked into every runtime.
2. Configuration: abstract MCP and skills manifests, compiled into each CLI's native format by `nexgen sync`.
3. Memory: the Markdown KnowledgeVault, versioned in Git, with hash-checked writes.

```
                         ┌──────────────┐
                         │  AGENTS.md   │ ──► [ BEHAVIOR ]
                         └──────┬───────┘
                                │
   ┌────────────────────────────┼────────────────────────────┐
   │                            ▼                            │
   │                    ┌───────────────┐                    │
   │                    │ neXgen Engine │                    │
   │                    └───────┬───────┘                    │
   │                            │                            │
   ▼                            ▼                            ▼
[ CONFIGURATION ]         [ SECRETS ]                   [ MEMORY ]
MCP Manifests / Skills    Age Multi-Recipient Store     KnowledgeVault
Claude · Codex ·          Zero-Passphrase (0600)        CAS Locked Git Notes
OpenCode · Antigravity    Per-Host OAuth Slots          Link Hygiene Map
```

Every file these commands create is listed in [what-gets-written.md](docs/what-gets-written.md). The one write outside your home directory is the server role's sudo policy, described in [roles.md](docs/roles.md).

## Quick start

### Option A, installed (recommended for daily use)

The package is not on PyPI yet, so the install comes from this repository:

```bash
uv tool install git+https://github.com/matteopasseri407/NeXgen-Engine   # or: pipx install git+https://github.com/matteopasseri407/NeXgen-Engine
nexgen info
nexgen doctor
```

A package install carries the whole engine (the Council, the MCP proxy, the hooks, the templates) and leaves the launchers to the package manager. It has no repository folder, so the Vault comes from the template repository:

```bash
git clone https://github.com/matteopasseri407/NeXgen-Engine.git ~/KnowledgeVault
nexgen init --local --root ~/KnowledgeVault          # add --check to look before writing
```

Update a package install with the manager that installed it. Releases publish a source archive, a wheel and `SHA256SUMS`; PyPI and Homebrew are documented but not active yet, see [release-packages.md](docs/release-packages.md).

### Option B, cloned (recommended for working on the engine)

```bash
git clone https://github.com/matteopasseri407/NeXgen-Engine.git ~/KnowledgeVault
cd ~/KnowledgeVault
# Pick the command for this machine's role (see docs/roles.md):
sh install.sh --role server      # headless Linux gateway, always on
sh install.sh --role node        # node joining an existing gateway
sh install.sh --role standalone  # local PC with its own gateway
# Windows PowerShell: .\install.ps1 -Role <server|node|standalone>
```

The installer runs the bootstrap. Verify it, then align and check whenever you change something:

```bash
nexgen sync
nexgen doctor --verbose
```

On a Git checkout, `nexgen update` asks for confirmation; the scheduled heartbeat can apply patch releases unattended. See [upgrade.md](docs/upgrade.md).

### Configure your environment

Open `INIT.md` and paste it into the agent CLI you already use: Claude Code, Codex, OpenCode or Antigravity. It walks through the profile and the modules.

## Platform support

<!-- platform-status:start -->

| System | Status | On what evidence |
|---|---|---|
| Linux | **released** | the platform this is developed and used on daily; the full cycle (install, alignment, doctor, grooming, council, update) runs here and in CI |
| Windows | **released** | verified on real hardware and in CI; full native Python execution, native command shims, and complete CLI alignment |
| macOS | **untested** | shares the POSIX paths with Linux and should work, but nobody has run it end to end; treat a failure here as expected, and reporting it as useful |

| Assistant | Status | What is covered |
|---|---|---|
| Claude Code | **complete** | instructions, MCP connectors, skills, guardrails |
| Codex | **complete** | instructions, MCP connectors, skills |
| OpenCode | **complete** | scope-file instructions, MCP connectors, native plugins/permissions, skills, and a Council seat |
| Antigravity | **complete** | instructions, MCP connectors, skills, and a Council seat; the Council adapter uses agy --print --model ... --disable-slash-commands --new-project --sandbox with the prompt on stdin; vendor isolation limits are documented in docs/council.md |

<!-- platform-status:end -->

## How it compares

|  | A folder of dotfiles | NeXgen Engine |
|---|---|---|
| What you edit | the files each CLI reads | one Vault: `AGENTS.md`, the MCP manifest, the skills manifest, the secrets file |
| What each CLI gets | the same text, copied | its own native format, generated by `nexgen sync` |
| Drift | you notice it, eventually | `nexgen doctor` exits non-zero and lists what broke |
| Secrets | in the dotfiles, or in your head | one age-encrypted file, one key per machine |
| A second PC | another copy to keep in step | the same source, applied on each machine |
| Memory | none | Markdown in Git, with hash-checked writes |

The engine writes configuration, reads what the harnesses leave on disk, and starts a CLI when you ask it to. It does not sit between a CLI and its model. The full contracts are in [architecture-contract.md](docs/architecture-contract.md) and [sync-contract.md](docs/sync-contract.md).

## FAQ

**Can I use this commercially?**
The repository uses PolyForm Noncommercial 1.0.0. See [LICENSE](LICENSE) for the terms and [COMMERCIAL.md](COMMERCIAL.md) if you need a commercial license.

**Does it work with my existing MCP servers?**
Yes. The sync is additive: a server in a CLI config that the manifest does not know is preserved and left in place, because it may be yours.

**What happens when doctor cannot check something?**
It says undetermined and keeps that separate from a failure. Add `--strict` if you want an unverifiable check to fail the run too.

## Development

Development happens on `developer`; releases reach `main` through a verified pull request. Maintainers ship with `nexgen release ship`, which runs the bump, the pull request, the checks, the signed tag and the sync in one command. The [branch contract](docs/agent-lanes.md) covers concurrent work and automatic alignment with `main`. Module owners and test requirements are in [CONTRIBUTING.md](CONTRIBUTING.md).

## License

PolyForm Noncommercial License 1.0.0. Free for noncommercial use, modification and self-hosted deployments. See `LICENSE` for the terms, and `COMMERCIAL.md` for commercial use.

# NeXgen Engine

<p align="center">
  <picture>
    <source srcset="assets/nexgen-architecture-banner.webp" type="image/webp">
    <img src="assets/nexgen-architecture-banner.png" alt="NeXgen Engine — AI Operating Layer" width="100%" loading="eager">
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
  <a href="README.it.md">🇮🇹 Leggi in italiano</a> · <a href="#quick-start">Quick Start</a> · <a href="#why-nexgen-vs-alternatives">Why NeXgen?</a> · <a href="docs/architecture-contract.md">Architecture</a> · <a href="CHANGELOG.md">Changelog</a>
</p>

**One Git repo that configures every AI coding CLI on every machine — and checks the result.**

NeXgen Engine is a deterministic control layer that keeps instructions, tool configuration, secrets, and version-controlled memory identical across Claude Code, Codex, OpenCode, and Antigravity.

Agent CLI configurations drift across machines. NeXgen keeps a single source of truth in Git, compiles it into each assistant's native format, and verifies the outcome with automated diagnostics that fail instead of passing silently.

---

## Demo

> Visual proof beats architecture diagrams. The two commands below are the whole product: see the state, fix the drift.

```bash
nexgen info    # visual dashboard: engine version, runtimes aligned, vault hygiene, secrets
nexgen shell   # interactive REPL [1-7] — manage everything without opening an AI assistant
nexgen doctor  # fail-closed checks: git alignment, MCP reachability, link hygiene, permissions
```

<p align="center">
  <picture>
    <source srcset="assets/nexgen-info-demo.webp" type="image/webp">
    <img src="assets/nexgen-info-demo.png" alt="nexgen info dashboard on Windows: Host, Vault, Planes & Runtimes, Modules and Security & Diagnostics at a glance" width="100%">
  </picture>
  <br><em><code>nexgen info</code> on Windows — Host, Vault (344 notes), Planes & Runtimes, Modules and Security & Diagnostics at a glance. Run <code>nexgen doctor</code> for full diagnosis.</em>
</p>

---

## The Three Planes

NeXgen structures agent operations into three decoupled planes:

1. **Behavior:** Universal operating policies, prompts, and invariant guardrails defined in `AGENTS.md` and symlinked into every runtime.
2. **Configuration:** Abstract MCP manifests and skills compiled deterministically into each CLI's native configuration format via `nexgen sync`.
3. **Memory:** Plain Markdown KnowledgeVault with compare-and-swap (CAS) concurrency locking, per-section updates (`update_section`), and automatic Git versioning.

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

---

## How it compares

Small syncers copy one MCP server fast. NeXgen covers the full layer when several CLIs on several machines must share instructions, MCP, skills, secrets and memory without drift.

| Capability | NeXgen Engine | AgentSync | mcp-sync | mcps-manager | dotfiles-ai |
|---|---|---|---|---|---|
| **MCP sync** | manifest `yaml` → native, 4 CLIs | symlink | auto-discover | bundle | — |
| **AGENTS.md / instructions** | canonical `AGENTS.md` + CAS | symlink | — | — | template |
| **Skills** | lazy catalog + `deps:` | yes | — | — | — |
| **Memory vault (Markdown+Git)** | CAS + `update_section` + `vault-map` | — | — | — | — |
| **Secrets `age` Zero-Passphrase** | multi-recipient `0600` + per-host OAuth | — | — | — | — |
| **Doctor diagnostics** | fail-closed checks (see CI) | — | — | — | — |
| **Windows native** | verified + CI + native shims | community | Python | Node | community |
| **Tests** | automated suite (see CI) | partial | — | — | — |
| **License** | PolyForm Noncommercial 1.0.0 | MIT | MIT | MIT | MIT |

*Capabilities as of Sep 2026 — corrections welcome. If you only need a lightweight MCP copy between two CLIs, a small syncer is the faster path. If you want zero drift across instructions, MCP, skills, secrets and memory with a doctor that fails closed, NeXgen covers all five in one place.*

---

## What it does

* **Single Python core (`nexgen_core`):** runs natively on Linux and Windows, no shell twins. Automated suite in CI.
* **Deterministic modules:** 9-module catalog (`memory`, `semantic-rag`, `firecrawl`, `ocr`, `n8n`, `browser`, `council`, `local-lane`, `sync`) managed with `nexgen modules list` and `nexgen modules set`.
* **Governed local lane (optional):** small local models run read-only through `nexgen-local` — engine-built queries, fail-closed audit receipts, and a blocking trap suite that fails on a single injection or confabulation. See `docs/local-lane.md`.
* **Secrets store:** asymmetric `age` encryption (`99-SECRETS/secrets.yaml.age`) on machine-local keys (`0600`), isolated per-host OAuth slots, materialized `secrets.env` for shells and systemd services. No passphrase to remember or type.
* **Operator shell:** `nexgen info` status dashboard and `nexgen shell` interactive REPL, so routine management never needs an AI assistant open.
* **Four runtimes:** Claude Code, Codex, OpenCode (native V2: scope-file instructions, `plugins`/`permissions`, skill views) and Antigravity, each rendered in its own dialect, Council seats included.
* **Fail-closed diagnostics (`nexgen doctor`):** automated checks over git alignment, manifest reachability, link hygiene, token presence and permission boundaries. A check that cannot verify reports undetermined instead of passing.

---

## Quick Start

### Option A — Installed (recommended for daily use)

```bash
# The engine is distributed from this repository (no PyPI package yet):
uv tool install git+https://github.com/matteopasseri407/NeXgen-Engine   # or: pipx install git+https://github.com/matteopasseri407/NeXgen-Engine
nexgen info
nexgen doctor
```

Once the maintainer registers a PyPI token or the Homebrew tap (see
`docs/release-packages.md`), `uv tool install nexgen-engine` and
`brew install matteopasseri407/nexgen/nexgen` become the shorter paths;
every release also ships an sdist, a wheel and `SHA256SUMS` as release
assets, so any installer can verify what it downloads.

Updates via `nexgen update` (with confirmation) and via the scheduled `guard` task that runs at login + every 30 min.

### Option B — Cloned (recommended for hacking the engine)

```bash
git clone https://github.com/matteopasseri407/NeXgen-Engine.git ~/KnowledgeVault
cd ~/KnowledgeVault
bash install.sh --check          # Windows PowerShell: .\install.ps1 -Check
```

### 1. Bootstrap

Already done by the installer. Verify:

```bash
nexgen sync
nexgen doctor --verbose
```

### 2. Configure your environment

Open `INIT.md` and paste its contents into your preferred agent CLI (Claude Code, Codex, OpenCode, or Antigravity). The agent will guide you through profile selection and module setup.

### 3. Align and verify (anytime)

```bash
nexgen sync
nexgen doctor
```

### 4. Interactive operator shell

```bash
nexgen info
nexgen shell
```

---

## Platform Support

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
| Antigravity | **complete** | instructions, MCP connectors, skills, and a Council seat; the seat was unblocked on 2026-08-22 with a stateless invocation (agy --model ... --disable-slash-commands --new-project --sandbox -p <prompt>) verified live with a nonce prompt |

<!-- platform-status:end -->


---

## Architecture Boundaries

* **No Lock-In:** All memories and configuration are stored as human-readable Markdown and YAML in Git.
* **Deterministic Write Paths:** Knowledge notes are modified exclusively via CAS hash verification to prevent race conditions.
* **Non-Invasive Execution:** The engine manages configuration as code above runtime execution; it does not intercept real-time model token streams.

See `docs/architecture-contract.md` and `docs/sync-contract.md` for the full contracts.

---

## FAQ

**Can I use this commercially?**
PolyForm Noncommercial 1.0.0 allows free noncommercial use, modification, and self-hosted deployments. Commercial use requires a separate agreement — see `COMMERCIAL.md` and `LICENSE`. The Python packaging and CLI tooling are intended to stay MIT-compatible; the engine's orchestration layer is noncommercial by design.

**How is this different from dotfiles?**
Dotfiles sync files. NeXgen syncs *semantics*: one `AGENTS.md`, one MCP manifest, one skills manifest — compiled to each CLI's native dialect (JSON/TOML/YAML, different paths on Linux vs Windows), with drift detection and fail-closed guardrails. A symlink farm cannot do that.

**Do I need all four CLIs?**
No. Install only what you use — `nexgen doctor` warns (not fails) for absent CLIs. Adding a runtime later is one `nexgen sync`.

---

## License

PolyForm Noncommercial License 1.0.0. Free for noncommercial use, modification, and self-hosted deployments. See `LICENSE` for details. For commercial inquiries see `COMMERCIAL.md`.

# Credits

NeXgen Engine builds on the work of others. This file lists the
third-party components and their authors. Each component keeps its own upstream
license; this file gives credit and points to the source.

## Self-hosted stack (deploy/)

| Component | Upstream | License | Purpose |
|---|---|---|---|
| **n8n** | https://github.com/n8n-io/n8n | Sustainable Use License | Workflow automation engine |
| **RapidOCR** | https://github.com/RapidAI/RapidOCR | Apache-2.0 | OCR engine (used in Vault OCR) |
| **Playwright MCP** | https://github.com/microsoft/playwright-mcp | Apache-2.0 | Browser automation over CDP |
| **FastAPI** | https://github.com/tiangolo/fastapi | MIT | Vault OCR API framework |
| **uvicorn** | https://github.com/encode/uvicorn | BSD-3-Clause | ASGI server |

## MCP servers (mcp/manifest.yaml)

| Server | Upstream | License |
|---|---|---|
| **firecrawl-mcp** | https://github.com/firecrawl/firecrawl-mcp | MIT |
| **@playwright/mcp** | https://github.com/microsoft/playwright-mcp | Apache-2.0 |
| **@modelcontextprotocol/server-filesystem** | https://github.com/modelcontextprotocol/servers | MIT |
| **@modelcontextprotocol/server-memory** | https://github.com/modelcontextprotocol/servers | MIT |

## Skills (skills/, see skills.manifest.yaml.example)

The eight starter skills the engine actually ships are all first-party,
vendored in this repo under `03-INFRA/agent-universal-layer/skills/`:

| Skill | Author | Upstream | License |
|---|---|---|---|
| **nexgen-update** | Matteo Passeri (this repo) | — | This repo's LICENSE |
| **vault-close** | Matteo Passeri (this repo) | — | This repo's LICENSE |
| **vault-council** | Matteo Passeri (this repo) | — | This repo's LICENSE |
| **vault-doctor** | Matteo Passeri (this repo) | — | This repo's LICENSE |
| **vault-groom** | Matteo Passeri (this repo) | — | This repo's LICENSE |
| **vault-map** | Matteo Passeri (this repo) | — | This repo's LICENSE |
| **vault-save** | Matteo Passeri (this repo) | — | This repo's LICENSE |
| **vault-update** | Matteo Passeri (this repo) | — | This repo's LICENSE (deprecated alias for `nexgen-update`, kept so an existing manifest keeps resolving) |

Third-party skills are not vendored: the manifest example only inventories
them by pinned commit (`origin: github`), so none ship inside this repo
today. Add a row here the day one actually does.

## Python libraries (scripts/ and ocr/)

| Library | License |
|---|---|
| PyYAML | MIT |
| google-api-python-client / google-auth | Apache-2.0 |
| Pillow | HPND |
| onnxruntime | MIT |
| LangGraph / langgraph-checkpoint-sqlite (`[local]`, `[council]` extras) | MIT |
| langchain-ollama (`[local]` extra) | MIT |
| mcp — https://github.com/modelcontextprotocol/python-sdk (the official MCP SDK; replaces the engine's own stdio MCP wire protocol in `nexgen_core/mcp_stdio.py`, so the bridge and `nexgen mcp check` both get its spawning, framing, handshake and reply correlation) | MIT (Copyright (c) 2024 Anthropic, PBC) |
| filelock — https://github.com/tox-dev/filelock (replaces the engine's own `msvcrt`/`fcntl` split in `nexgen_core/lock.py`) | MIT |
| psutil — https://github.com/giampaolo/psutil (reads and stops the process table of `nexgen_local` supervision, in place of the engine's own `/proc` reader and per-question CIM call) | BSD-3-Clause |
| tomlkit — https://github.com/python-poetry/tomlkit (the line-preserving TOML round-trip of the Codex renderer in `nexgen_core/mcp_render/codex.py`, in place of the engine's own line scanner, key quoter and value writer) | MIT |

## Optional external tools

These are separate programs the engine calls when they are installed, never
dependencies of it, and the numbers they produce are theirs:

| Tool | Upstream | License | Purpose |
|---|---|---|---|
| **gitleaks** | https://github.com/gitleaks/gitleaks | MIT (Copyright (c) 2019 Zachary Rice) | The scanner behind the engine's own commit gate: `leak-scan/leak_scan.py` maps its four modes onto `gitleaks git --staged/--pre-commit/--log-opts`, `gitleaks dir` and `gitleaks stdin`, so the engine keeps only its rules and the private deny-list. It replaces the engine's own scanner for that gate (`published_exceptions.yaml` became a gitleaks baseline). The gate refuses to run when the binary is absent, and says how to install it. The Council's egress gate and its output redaction never use it: they keep the built-in matcher and `leak_patterns.yaml`, because they must work on an installation that has no maintainer binary |
| **tokscale** | https://github.com/junhoyeo/tokscale | MIT (Copyright 2025 Junho Yeo) | Optional quota backend: reads the logins stored on this machine and reports each provider's own remaining quota to `nexgen quota` |
| **ccusage** | https://github.com/ccusage/ccusage | MIT (Copyright 2025 Junho Yeo) | Optional spend backend, two readers: reads the bot's own transcripts, JSONL and per-agent SQLite, and reports what it spent per day and per session to `nexgen openclaw cost` and one doctor line; reads the harness archive `opencode.db` with fork deduplication and prices each session, replacing the engine's flat sum of the recorded `cost` column behind the 5h, 7d and 30d windows of `sup costi`, `nexgen usage` and `nexgen quota` |
| **uv** | https://github.com/astral-sh/uv | MIT OR Apache-2.0 (Copyright (c) 2025 Astral Software Inc.) | Optional environment builder: when it is on `PATH`, `uv venv` and `uv pip install` create and fill the engine's runtime in `nexgen runtime ensure`, replacing the venv call, the pip bootstrap and the pip flags. Without it the engine uses `python -m venv` and `python -m pip` |
| **OpenClaw** | https://github.com/openclaw/openclaw | MIT (Copyright (c) 2026 OpenClaw Foundation) | The harness the engine configures, and the gateway client: `openclaw gateway call <method> --params <json> --json --timeout <ms>` resolves the URL, authenticates with the gateway's token and requests each method's operator scopes, replacing the engine's own WebSocket JSON-RPC client (`gateway_rpc.py`) and the `websockets` dependency it needed. It is also the node client and the approvals judge: `openclaw connect <target> --service --session-host --target-file <path>` pairs, installs the user service and confirms the first authenticated connection before it returns, replacing the engine's own attach poll (`node_attach.py`'s `wait_for_node_connection` and `check_node_connected`), and `openclaw approvals get --node <id> --json` returns the requested `tools.exec` policy, the node's approvals file and the merged `effectivePolicy` per agent scope, replacing the engine's hand-written precedence and ranking tables over that document. It is also the updater the engine follows the pin with: `openclaw update --tag <version> --json --yes` stages the candidate, has it admit the live installation, migrates the agents' databases, restarts the gateway, verifies process, health and plugins, rolls back a release that does not come back and records every run for `openclaw update status`, replacing `version_align.py`'s `npm install -g` invocation, its npm-and-prefix resolution and the gateway migration of `gateway_doctor.py`. Its plugin API is also what the engine's own plugin reads: `api.pluginConfig` is OpenClaw's own parse and validation of `plugins.entries.nexgen.config`, replacing the hand-written JSON read of a sidecar file, and `api.rootDir` / `api.resolvePath` say where the plugin was loaded from, so a job asks the host before searching its folder and the ancestors of a build copy (`engine-files.mjs`, `engine-config.mjs`) |
| **skills** | https://github.com/vercel-labs/skills | MIT (Copyright (c) 2026 Vercel, Inc.) | Optional fetch backend for github-origin skills: `npx skills@1.8.1 add owner/repo@<commit> --skill <name> --copy` resolves the pinned commit and downloads the one skill folder, replacing the engine's clone of the whole repository. Its `skills-lock.json` (version 2, `computedHash`) is read as the integrity check of the bytes that arrived, and the same `computedHash` is recomputed here by `nexgen_core/skill_hash.py` (algorithm and test vectors from `src/skill-hash.ts` and `tests/skill-hash.test.ts`), which is what makes a skill view that no longer holds its library's bytes a finding instead of an mtime guess. The engine keeps placement, exposure, the index and the pin, and a machine without Node keeps the clone |

## Maintainer CI (this repository only)

Neither of these is a dependency of the engine, and neither runs on a user's
machine: they serve the repository that publishes it.

| Tool | Upstream | License | Purpose |
|---|---|---|---|
| **Dependabot** | GitHub (built in) | — | The dependency updates of the repository itself: one grouped weekly pull request per ecosystem, against `developer`, configured in `.github/dependabot.yml` |
| **osv-scanner** | https://github.com/google/osv-scanner | Apache-2.0 | The vulnerability report of the `self-check` job: reads the declared dependencies and prints what OSV knows about them. Pinned by release and by SHA-256, report-only, and never a required check |

## Thanks

This project stands on the shoulders of the open-source and source-available
community above. If you extend NeXgen Engine with a new component, add it to this
file so credit stays accurate.

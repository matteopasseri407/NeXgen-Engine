# Security

## Reporting a vulnerability

Open a private security advisory on GitHub (Security tab of this repo) or email the address on the maintainer's GitHub profile. Do not open a public issue for anything that could expose a real credential or an active exploit path. A regular bug that doesn't touch secrets or code execution is fine as a normal issue.

## What must never be committed

- Anything under `99-SECRETS/` except `README.md`, `.gitkeep`, and `secrets-registry.md`. The `.gitignore` already blocks the rest, but double-check before force-adding files in that folder.
- Real API keys, tokens, SSH keys, webhook secrets, or tunnel credentials, in any file, including MCP manifests, `.env` files, or example configs. `03-INFRA/deploy/*/.env.example` files must only ever contain placeholders.
- Any personal data belonging to you or a third party: real names tied to private context, private chat IDs, internal project numbers, customer data. If you fork this repo and adapt it for yourself, keep that kind of detail out of the parts you intend to push publicly.

If you think you've already committed one of these, treat it as a leak: rotate the credential first, then clean the git history (not just the latest commit) before doing anything else.

## Trust boundaries

- **The vault itself is a set of plain files.** Any agent CLI with filesystem access to it can read and write everything inside. There is no per-file access control beyond your OS permissions.
- **`agent-sync`/`agent-doctor` run with your user's permissions.** They read and patch CLI config files (see `docs/what-gets-written.md`) and, in MULTI profile, install a systemd user timer. They do not use sudo and do not touch files outside your home directory except through the CLI config paths documented there.
- **MCP servers run as local processes or connect to your own VPS.** None of the tools in the default manifest send vault content to a third-party model or SaaS API as part of normal operation; the semantic search, OCR, and scraping services are self-hosted. If you add a hosted MCP server yourself, that server's own privacy and security posture applies.
- **The browser MCP attaches to a real, visible Chrome window over the DevTools protocol.** Agents are expected to never launch a headless browser behind your back; if you see one, that's a bug, not a feature.
- **Cloud-Server mode reaches your VPS over an SSH tunnel you configure.** The tunnel ports and credentials live in your own `99-INDEX/USER-PROFILE.md` and `99-SECRETS/`, not in this repo.

## Supported versions

This project does not yet follow a formal LTS/patch schedule. Security fixes land on `main`; there are no older release branches receiving backports at this time.

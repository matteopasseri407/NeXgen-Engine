# Shared Agent Browser (CDP, no-headless)

Runbook for the **mandatory** browser convention in `AGENTS.md` → `# Browser`.
Hard rule: agents attach to ONE shared, VISIBLE Chrome over CDP. Never headless, never their own browser, never hidden actions. Auto-start it; do not make the user ask.

## Why this exists

Chrome 136+ blocks `--remote-debugging-port` on the standard default profile directory. The stable solution is one personal working profile stored in a non-default CDP-capable path, with every browser launcher routed to it. Separate daily and agent profiles are forbidden because they split tabs, cookies and logins.

## Setup principle

The user has ONE Chrome profile that serves both daily browsing and agent work. Agents attach to it over CDP (`http://localhost:9222`) and reuse the visible window. The profile lives in a non-default path launched with CDP enabled. All browser launchers (desktop, dock, URL handlers) route to this single profile.

The exact paths, launchers, and repair scripts depend on the OS and are configured during the guided install (`INIT.md`). The invariant is: one profile, CDP-enabled, visible, shared.

## MCP wiring

Each CLI agent (Claude Code, Codex, <cheap-cli>, etc.) drives the browser via the Playwright MCP, pinned to the shared visible Chrome:

- `--cdp-endpoint http://localhost:9222` and **no** `--headless`.
- A pinned, patched MCP copy is recommended over `npx @latest` to avoid upstream changes that close the attached browser on client disposal.
- Restart an agent after config changes so it reloads the MCP.

## Scraping / search lane (no local browser)

Read-only scraping/search goes through `firecrawl`, self-hosted on the remote backend (when configured), reached via local SSH tunnel — server-side, no local browser. Local headless is allowed only as a deliberate exception (read-only, anonymous, when firecrawl doesn't fit), never as a habit and never for interactive/authenticated/state-changing work — that always stays in the visible shared Chrome. In a Local-Only setup, firecrawl is absent and native CLI search is the default.

## Procedure for any agent (every web task)

1. Check it is up: `curl http://localhost:9222/json/version` (Linux/Mac) or `Invoke-RestMethod http://localhost:9222/json/version` (Windows).
2. If not up, start it (local + reversible, no approval): run the launcher configured during install, then poll the port until it answers.
3. Attach and reuse the visible window — never `launch()`:

```python
# Playwright (Python)
browser = p.chromium.connect_over_cdp("http://localhost:9222")
context = browser.contexts[0]
page = context.pages[0] if context.pages else context.new_page()
```

```python
# browser-use (model-agnostic)
from browser_use import Agent, Browser
browser = Browser(cdp_url="http://localhost:9222")
```

## Login state

The shared profile is the user's synced personal profile, not an agent clone. If a site requests authentication again, complete it once in the visible shared Chrome; agents continue using the same persistent profile.

## Recovery

Back up the canonical profile directory before any rebuild. Do not copy from the default Chrome data directory if it has been junctioned/symlinked to the canonical profile — it is the same directory.

## Linux/Mac notes

- The launcher (e.g. `agent-chrome '<URL>'`) is the only visible Chrome entry point, the dock entry, and the default handler for HTTP/HTTPS/HTML. Plain Chrome launchers are hidden and redirected to the same wrapper.
- No login autostart by design (laptop battery). The user or any agent starts the same browser on demand.
- Pass `--class=Google-chrome` (or the equivalent for the DE) to the Chrome binary to prevent the dock from splitting the pinned icon when a custom user-data-dir changes the WM_CLASS.

## Windows notes

- The shared Chrome runs at `http://localhost:9222`, bound to `127.0.0.1`.
- A self-repair script restores CDP arguments on the main user shortcuts and URL handlers if Chrome updates rewrite them.
- `BackgroundModeEnabled=0` prevents a background process without CDP from winning the first-process race.

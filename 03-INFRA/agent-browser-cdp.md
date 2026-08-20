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

## The first-process race, which decides everything else

Whichever Chrome process opens the shared profile **first** decides whether the
CDP port exists for the rest of that session. A launcher that starts Chrome
without `--remote-debugging-port` wins that race, and every later launch — the
wrapper included — is reduced to Chrome's single-instance IPC handoff, which
cannot add a port to a browser that is already running.

The failure is silent by construction: the browser opens, the page works, and
only the agents are locked out. So *every* entry point that can start Chrome
must route through the launcher, and `agent-doctor` reports any that does not.

## Two callers, two contracts

The launcher serves two different intents and must not conflate them:

| Call | Meaning | Behaviour |
| --- | --- | --- |
| `agent-chrome [args]` | someone wants a **window** | always hands off to Chrome, so a window always appears |
| `agent-chrome --ensure` | an agent wants the browser to **exist** | exits 0 when CDP already answers, opening nothing |
| `agent-chrome --heal` | the race was lost | restarts Chrome to restore the CDP port |

A bare call used to mean `--ensure`. That is why clicking the Chrome icon could
do nothing at all: with a windowless Chrome still holding the port, the launcher
exited 0 and the desktop treated the launch as successful.

## Installed web apps are not spare tabs

A shared Chrome exposes every installed web app (WhatsApp, n8n, ChatGPT…) over
CDP as an ordinary `page` target, indistinguishable from a tab. Two consequences,
both handled:

- Chrome's generated `chrome-<app-id>-<profile>.desktop` launchers call the
  Chrome binary directly, so starting a web app first loses the race above.
  `agent-sync` rewrites their `Exec=` lines to the wrapper, and re-applies that
  on every `guard` run because Chrome regenerates them on install/update.
- Upstream `@playwright/mcp` adopts whichever target it enumerates first as the
  current tab, so an agent would drive — and navigate away — the human's
  application window. Hiding those windows would be the wrong fix: an open
  WhatsApp window *is* the right place to send a WhatsApp message. The line is
  between **using** an app window for its own app and **adopting** it as a
  general browser. The `playwright-human-safe.mjs` wrapper classifies pages by
  `display-mode` and enforces exactly that: an app window stays listed and can
  be selected deliberately, it is never adopted implicitly as the current tab,
  and it cannot be navigated off its own origin.

## Linux/Mac notes

- The launcher (e.g. `agent-chrome '<URL>'`) is the only visible Chrome entry point, the dock entry, and the default handler for HTTP/HTTPS/HTML. Plain Chrome launchers are hidden and redirected to the same wrapper.
- The MULTI provisioner installs the cross-platform `agent-chrome` command (implemented in Python via `nexgen_core.tools.chrome`). On Linux it also installs the visible `agent-chrome.desktop` entry, a hidden per-user `google-chrome.desktop` compatibility redirect, and the web-app launcher rewrite above, so no old dock, system or web-app launcher can win the first-process race without CDP. The launcher refuses to invent a second daily profile when it finds an unmigrated standard Chrome profile. Selecting `agent-chrome.desktop` as the host's default browser remains an explicit, reversible per-host step and is verified by `agent-doctor`.
- The launcher passes `--class=Google-chrome` only for real browser windows. A web-app launch keeps Chrome's own `crx_<app-id>` window class, so each installed app keeps its own dock icon.
- No login autostart by design (laptop battery). The user or any agent starts the same browser on demand.
- Pass `--class=Google-chrome` (or the equivalent for the DE) to the Chrome binary to prevent the dock from splitting the pinned icon when a custom user-data-dir changes the WM_CLASS.

## Windows notes

- The shared Chrome runs at `http://localhost:9222`, bound to `127.0.0.1`.
- A self-repair script restores CDP arguments on the main user shortcuts and URL handlers if Chrome updates rewrite them.
- `BackgroundModeEnabled=0` prevents a background process without CDP from winning the first-process race.

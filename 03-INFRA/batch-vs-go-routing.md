# Batch vs Direct-API Routing

Detail note loaded on demand from the agent bootstrap. Load this only when deciding whether to offload a heavy/batch load off the cheap CLI plan onto a direct cheap API.

## Principle

On a flat-fee cheap CLI plan, the resource to protect is the **windowed cap**, not the cost per token (it is flat). The direct cheap API serves when the CLI plan cannot reach it, or when a heavy load would eat the cap.

**Flag "batch-via-API" and stop to signal the user ONLY when:**

- The consumer is not the cheap CLI (automations, scripts, external pipelines): the CLI plan does not serve them, they must go direct.
- It is a large repetitive batch without tools (multi-item translation/extraction/classification, audits across many files, voluminous log sweeps) that would devour a sensible slice of the windowed cap: offloading it to the direct API preserves the cap for interactive work and avoids a stall.
- You are near saturating the cap: shift the bulk to the direct (uncapped) API instead of blocking.

**Stay on the cheap CLI plan (do NOT signal) for:**

- Interactive work with tools (MCP, filesystem, browser, automations via the CLI, git), debugging, architecture, sessions with persistent context.
- Any small or ordinary task: inside the cap it is already paid by the flat fee; moving it to the direct API adds metered expense and friction, not savings.

Anti-ambiguity rule: do not flag at every mechanical task. Flag only *large* batches (many items / much volume) or anything outside the cheap CLI. In doubt, stay on the CLI plan.

## Direct API key

Env var (e.g. `<DIRECT_API_KEY>`), indexed in `99-SECRETS/secrets-registry.md`. Never write the value in notes. The exact var name and provider are configured in `99-INDEX/USER-PROFILE.md`.

# Org / shared-backend deployment

For anyone considering NeXgen Engine as infrastructure for more than one
person — specifically the case where several people would share one
Cloud-Server backend (one VPS running n8n, Firecrawl, OCR, semantic search).
Read this before wiring more than one person's agent CLI into the same
backend. For the mono-user limits of the vault itself (one profile, one
secrets passphrase, no per-person boundary), see `docs/team.md`; this file
is specifically about the shared-backend case.

## What's actually supported today

The supported model is one vault per person. Each person runs their own
clone, answers their own `INIT.md` interview, and — if they use Cloud-Server
mode — points it at infrastructure they alone control. There is no
supported design where several people share one Cloud-Server backend or one
vault as if it were a multi-tenant system with per-person boundaries.

## The shared-VPS risk, spelled out

If an organization stands up a single Cloud-Server backend and lets several
employees' agent CLIs reach it over their own SSH tunnels, every one of
those CLIs gets full read/write access to whatever that backend exposes,
not a scoped slice of it.

`SECURITY.md` already states, about a single vault: "Any agent CLI with
filesystem access to it can read and write everything inside. There is no
per-file access control beyond your OS permissions." That statement applies
with equal force to a shared VPS: nothing in this framework adds per-user
scoping to the n8n workflows, the Firecrawl/OCR/semantic-search services, or
any vault mirrored through that backend. One employee's agent CLI can read
another employee's notes and, if the secrets archive or registry is
reachable from there, another employee's secrets too.

This is not a missing setting to flip; it is the current architecture.
Treat a Cloud-Server backend shared across people as full mutual trust among
everyone whose CLI can reach it — comparable to giving them all the same
root password. Doing this today is entirely at your own risk, and it is a
commercial use of the software (see `COMMERCIAL.md`) once it is part of
paid or revenue-generating work inside a company.

## A starting sketch for an IT rollout

This is a starting sketch, not a ready rollout tool: nothing in this repo
automates it, and it has not been exercised at real multi-person org scale.
Treat it as a shape to adapt, not a runbook to follow verbatim.

If an IT referent wants to give N people governed agent setups without
sharing one backend, the lower-risk shape today is N independent MINIMAL
installs, not one shared install:

1. **One vault clone per person.** `git clone` the repo into its own folder
   (or its own machine) for each person. Each clone is its own trust
   boundary; nobody's CLI has a path into anyone else's clone.
2. **One filled-in `99-INDEX/USER-PROFILE.md` per person.** Run `INIT.md`
   separately for each clone so every person answers their own interview —
   same template, their own answers (their own hardware, their own CLI
   choice, their own architecture decision).
3. **No shared VPS.** If a person needs Cloud-Server mode, give them
   infrastructure only they control — their own VPS, or an isolated
   account/namespace provisioned so their tunnel and secrets can't reach
   anyone else's. Pointing two people's clones at the same VPS re-creates
   the risk above.
4. **A minimal per-person checklist:** their own `99-SECRETS/` archive and
   passphrase, never shared with anyone including IT; their own filled-in
   `USER-PROFILE.md`; and confirmation that their CLI's MCP servers point
   only at infrastructure they alone control.

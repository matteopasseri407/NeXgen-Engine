# Org / shared-backend deployment

🇮🇹 [Italiano](it/org-deployment.md)

For anyone considering NeXgen Engine as infrastructure for more than one person, specifically the case where several
people share one Cloud-Server backend (one VPS running n8n, OCR and semantic search). Read this before wiring a second
person's agent CLI into the same backend. For the single-user limits of the vault itself (one profile, one secrets
passphrase, no per-person boundary) see [team.md](team.md); this file covers the shared backend.

## What is supported today

One vault per person. Each person runs their own clone, answers their own `INIT.md` interview and, in Cloud-Server
mode, points it at infrastructure they alone control. Nothing supports several people sharing one Cloud-Server backend
or one vault as if it had per-person boundaries.

## The shared-VPS risk

If an organization runs a single Cloud-Server backend and lets several employees' agent CLIs reach it over their own
SSH tunnels, every one of those CLIs gets full read and write access to everything that backend exposes.

`SECURITY.md` says of a single vault: "Any agent CLI with filesystem access to it can read and write everything
inside. There is no per-file access control beyond your OS permissions." That applies unchanged to a shared VPS:
nothing here adds per-user scoping to the n8n workflows, to the OCR and semantic-search services, or to any vault
mirrored through that backend. One employee's CLI can read another employee's notes and, if the secrets archive or
registry is reachable from there, another employee's secrets.

No setting fixes this. A Cloud-Server backend shared across people means full mutual trust among everyone whose CLI
can reach it, comparable to giving them all the same root password. Doing it is at your own risk, and once it becomes
paid or revenue-generating work inside a company it is a commercial use the licence does not cover and no commercial
licence is offered for (see `COMMERCIAL.md`).

## A sketch for an IT rollout

This is a starting shape, not a rollout tool: nothing in the repo automates it, and nobody has run it at real
multi-person scale.

For N people who need governed agent setups without sharing one backend, the lower-risk shape today is N independent
MINIMAL installs:

1. **One vault clone per person.** Clone the repo into its own folder, or onto its own machine, for each person. Each
   clone is its own trust boundary, and nobody's CLI has a path into anyone else's clone.
2. **One filled-in `99-INDEX/USER-PROFILE.md` per person.** Run `INIT.md` separately for each clone, so everyone
   answers their own interview: same template, their own hardware, CLI choice and architecture decision.
3. **No shared VPS.** If someone needs Cloud-Server mode, give them infrastructure only they control, either their own
   VPS or an isolated account or namespace provisioned so their tunnel and secrets cannot reach anyone else's.
   Pointing two people's clones at one VPS recreates the risk above.
4. **A short per-person checklist:** their own per-machine credential storage (OS keyring, environment variables),
   never shared with anyone including IT; `99-SECRETS/` as a git-ignored local space with only the non-sensitive
   registry tracked; their own filled-in `USER-PROFILE.md`; and confirmation that their CLI's MCP servers point only
   at infrastructure they alone control.

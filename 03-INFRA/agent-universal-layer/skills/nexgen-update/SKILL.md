---
name: nexgen-update
description: Update NeXgen Engine itself. Check whether a newer engine release exists and, on explicit confirmation, upgrade this machine to it and verify the result. Use when the user asks to update or upgrade the engine, or after a release announcement.
---

# Update the engine

Use the real cross-platform `nexgen update` command (historical name
`nexgen-update`, still installed) and keep its confirmation gate intact.

1. Run `nexgen update --check` (the same as `nexgen upgrades`).
   This check-only mode fetches release metadata and prints the installed
   version, newest released tag and exact changelog entries without moving the
   branch or changing installed files.
2. Summarize the release in plain language.
   Call out every `### Changed` or `### Removed` entry explicitly.
3. Stop and ask before touching anything.
   State the exact target tag and that the command will merge it, run
   provisioning and compare the doctor before and after.
   In a split install, mention that an existing private engine pin is committed
   through `vault-push`; no other data file is staged.
4. On explicit confirmation, run `nexgen update --yes`.
   Do not stash, commit, reset or clean user work to make its preflight pass.
5. If neither name is on this machine because it predates the release that
   introduced the command, follow the manual bootstrap in `docs/upgrade.md` once.
   The provisioning pass then installs the command for future releases.
6. Reconcile the skills the release brought with it.
   The engine's own commands are `origin: engine`, so they travel with the
   upgrade and need nothing: whatever the release changed in them is already
   live. What needs a look is the manifest, when a release renamed or removed a
   command. Run `nexgen doctor` and read its line about engine-owned skills: a
   rename that ships a deprecated stub resolves silently, a removal is reported
   by name. Only then decide with the user whether to update or drop the entry.
   Skills the user owns (`origin: vault`) are never touched by an upgrade.
7. Report the result and remind the user that every other machine must run the
   same update.
8. A second install is never upgraded by this command; say so plainly.
   On Cloud-Server that is the VPS: the redeploy runbook is
   `03-INFRA/deploy/README.md`, section "Upgrading the server side".
   Do not run it for the user because restarting those containers interrupts
   every agent using the vault.
   On Local-Full the same connectors run in Docker on this machine, and they
   are not restarted either: `nexgen stack up` re-applies the current compose
   definitions, and `nexgen stack status` says what is actually running.

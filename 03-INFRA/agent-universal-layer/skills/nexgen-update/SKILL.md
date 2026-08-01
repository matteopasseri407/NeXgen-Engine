---
name: nexgen-update
description: Update NeXgen Engine itself. Check whether a newer engine release exists and, on explicit confirmation, upgrade this machine to it and verify the result. Use when the user asks to update or upgrade the engine, or after a release announcement.
---

# Update the engine

Use the real cross-platform `nexgen-update` command and keep its confirmation
gate intact.

1. Run `nexgen-update --check`.
   This check-only mode fetches release metadata and prints the installed
   version, newest released tag and exact changelog entries without moving the
   branch or changing installed files.
2. Summarize the release in plain language.
   Call out every `### Changed` or `### Removed` entry explicitly.
3. Stop and ask before touching anything.
   State the exact target tag and that the command will merge it, run
   provisioning and compare the doctor before and after.
4. On explicit confirmation, run `nexgen-update --yes`.
   Do not stash, commit, reset or clean user work to make its preflight pass.
5. If the bare command is absent because this machine predates the release
   that introduced it, follow the manual bootstrap in `docs/upgrade.md` once.
   The provisioning pass then installs the command for future releases.
6. Report the result and remind the user that every other machine must run the
   same update.
7. On Cloud-Server, say plainly that the VPS is a second install and was not
   upgraded by the workstation command.
   The redeploy runbook is `03-INFRA/deploy/README.md`, section "Upgrading the
   server side".
   Do not run it for the user because restarting those containers interrupts
   every agent using the vault.

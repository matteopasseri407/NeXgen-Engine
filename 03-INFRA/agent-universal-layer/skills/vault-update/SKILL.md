---
name: vault-update
description: Deprecated alias for nexgen-update. Updates the NeXgen ENGINE, not the Vault. Use when the user typed /vault-update out of habit; prefer nexgen-update.
---

# Renamed to `nexgen-update`

This command updates **the engine**, not the Vault. The `vault-` prefix said
the opposite, so the skill is now `nexgen-update`. This entry stays so an
existing `skills.manifest.yaml` and anyone's muscle memory keep working.

Do not follow a runbook from here — there is deliberately only one copy, so
the two names cannot drift apart:

1. Load the `nexgen-update` skill and follow it exactly.
2. Mention once, in passing, that the command is now `nexgen-update`. Do not
   make a lesson of it and do not stop to ask.

Nothing in the Vault is touched by either name. To update the Vault's own
contents you want `vault-groom` (consolidate notes) or `vault-close`
(distill a session into notes).

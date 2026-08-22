# 99-SECRETS

Local secret space for this vault. **Everything in this folder is git-ignored
except three files**: this `README.md`, `.gitkeep`, and `secrets-registry.md`.
Never commit plaintext secrets.

## What ships

- `secrets-registry.md` — the non-sensitive index. Lists *which* secrets
  exist: name, provider, the env var they map to, scope, last-rotated date.
  **Never any values.** Tracked in git so the map stays in sync across
  machines.
- Anything else you put here (values, backups, scratch) is local-only and
  never leaves the machine.

## Where values live

This framework does not ship a secret manager. Real credentials (passwords,
API keys, tokens, SSH keys) stay where your machine keeps them: the OS
keyring, per-user environment variables, or per-CLI credential stores. The
registry above only points at them. If more than one person uses this
vault, see `docs/team.md`: sharing a clone shares config and notes, never
credentials.

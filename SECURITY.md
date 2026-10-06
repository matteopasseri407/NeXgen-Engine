# Security

## Reporting a vulnerability

Open a private security advisory through this repository's Security tab, or email the address on the maintainer's GitHub profile.
Use a private report for exposed credentials or an active exploit path.
Other bugs can be reported in a public issue.

## What must never be committed

- Anything under `99-SECRETS/` except `README.md`, `.gitkeep`, and `secrets-registry.md`. The `.gitignore` already blocks the rest, but double-check before force-adding files in that folder.
- Real API keys, tokens, SSH keys, webhook secrets, or tunnel credentials, in any file, including MCP manifests, `.env` files, or example configs. `03-INFRA/deploy/*/.env.example` files must only ever contain placeholders.
- Any personal data belonging to you or a third party: real names tied to private context, private chat IDs, internal project numbers, customer data. If you fork this repo and adapt it for yourself, keep that kind of detail out of the parts you intend to push publicly.

If you think you've already committed one of these, treat it as a leak: rotate the credential first, then clean the git history (not just the latest commit) before doing anything else.

## Trust boundaries

- **The vault itself is a set of plain files.** Any agent CLI with filesystem access to it can read and write everything inside. There is no per-file access control beyond your OS permissions. If more than one person would share a Cloud-Server backend, see `docs/org-deployment.md` for what that means in practice.
- **`agent-sync`/`agent-doctor` run with your user's permissions.** They read and patch CLI config files (see `docs/what-gets-written.md`) and, in MULTI profile, install a systemd user timer (Linux) or a Task Scheduler entry (Windows). They do not use sudo/admin elevation and do not touch files outside your home directory except through the paths documented there.
- **MCP servers run as local processes or connect to your own VPS.** None of the tools in the default manifest send vault content to a third-party model or SaaS API as part of normal operation; the semantic search, OCR, and scraping services are self-hosted by you, on infrastructure you deploy and own (see `03-INFRA/deploy/`), not a service this project or its author runs for you. If you add a hosted MCP server yourself, that server's own privacy and security posture applies.
- **The browser MCP attaches to a real, visible Chrome window over the DevTools protocol.** Agents are expected to never launch a headless browser behind your back; if you see one, that's a bug, not a feature.
- **Cloud-Server mode reaches your VPS over an SSH tunnel you configure.** The tunnel ports and credentials live in your own `99-INDEX/USER-PROFILE.md` and `99-SECRETS/`, not in this repo.

## Two different leak-scans, two different audiences

The engine ships one shared secret-detection module
(`03-INFRA/agent-universal-layer/leak-scan/leak_scan.py`), but it backs two
gates with different scopes:

- **Council's egress/output scan is an end-user protection, always on.**
  Every `council.py` call scans the outgoing brief (and the text a seat sends
  back) for likely secrets before it can reach, or come back from, a
  third-party model seat. This runs for every user, every session, with no
  opt-out beyond not using Council. See `docs/council.md`'s Guardrails
  section.
- **The CI leak-scan is the repository publishing gate.** It runs on every
  pull request and protects the public history from likely secrets and
  personal paths. Normal users never publish engine code; this has nothing to
  do with their private vault data or their Council sessions.
  GitHub branch protection is the enforcement boundary. `main` requires
  passing CI, signed commits, and no force pushes or branch deletion. It does
  not require an approving review, because a single maintainer cannot approve
  their own pull request and a rule nobody can satisfy is a rule that gets
  turned off. Local developer conveniences are not security controls and do
  not ship with the product.

## Supported versions

This project does not yet follow a formal LTS/patch schedule. Security fixes land on `main`; there are no older release branches receiving backports at this time.

## Release signing

Every commit on `main` from `8fcd351` (2026-07-08) onward carries a verifiable
signature (`git verify-commit`). Release tags are signed with `git tag -s` on a
maintainer machine and verified with `git verify-tag`; both OpenPGP and SSH
signature formats are accepted.

From `v0.98.0` onward, `release.yml` requires an annotated tag containing a signature block.
It checks the presence of the signature, not who made it. Cryptographic verification happens where the keys are: on installed copies when they update (see below), and on a maintainer or auditor machine.

Earlier tags are not a uniform baseline, and this section previously claimed
they were. Verify before you trust one:

- `v0.1.0`–`v0.3.0` predate the signing discipline and are unsigned.
- `v0.5.0`, `v0.5.1` are unsigned and `v0.5.2` is a lightweight tag, not a tag
  object.
- `v0.93.0`–`v0.97.6` are unsigned. From `v0.93.0` the release workflow created
  the tag on a CI runner, which holds no signing key, and `--verify-tag` only
  confirms that a tag exists. Twelve releases shipped that way before the gap
  was found on 2026-08-06.
- Everything else from `v0.3.1` onward is signed.

Unsigned tags after `v0.3.0` violate the release policy.

### What an installed copy verifies

`nexgen-update` verifies the **tag object** of the release it is about to
install, not the commit the tag points at (for a release that commit is a merge
made by GitHub, whose signature says nothing about the maintainer). The check
is made against the signers pinned in `03-INFRA/agent-universal-layer/trust/`
**of the copy that is already installed**, never of the release being
installed: a release cannot vouch for itself. OpenPGP checks run in a throwaway
keyring holding only the pinned keys, so the machine's own keyring is neither
trusted nor changed.

| Outcome | Meaning | Interactive | `--unattended` |
| --- | --- | --- | --- |
| verified | signed by a pinned key | installs | installs |
| bad | the signature does not match the tag, or its key is revoked | refused | refused |
| untrusted | a valid signature, by a key the install does not pin | refused | refused |
| unverifiable | unsigned or lightweight tag, no anchor installed yet, `gpg`/`ssh-keygen` missing, pinned key expired | warns, you decide | refused |

`--check` reports the same verdict and refuses `bad` and `untrusted` too.

Pinned signers today (the keys that signed the `v2.x` releases):

- OpenPGP `5D06 1CE9 626C 9CC8 BD88  761F 4399 A81E 895E B96F`
- OpenPGP `6CD1 92BE 0A78 7F77 867F  3B9F 9115 22B8 F02F FA88`
- SSH `SHA256:zCkqYfvAjmrD+kCDQ9Jp0plP8fV89mM+GfavKOmGvsA`

What this does **not** cover, stated plainly:

- A copy installed before `trust/` existed has no anchor. The update that first
  brings `trust/` is still judged by the updater that was installed, which only
  warned. The guarantee starts with the update after it.
- A compromised maintainer key or maintainer machine. The anchor decides whose
  signature counts; it cannot tell whether the signer was coerced or breached.
- `release.yml` still only checks that a tag *carries* a signature block. Before
  publishing, run
  `python3 03-INFRA/scripts/nexgen_core/release_trust.py vX.Y.Z --trust-dir <trust/ of the previous release>`:
  it exits non-zero if installed copies would refuse the tag.

Changing a signer takes two releases: ship the new key in a release signed by a
key that is already pinned, and only then sign with the new one. A release
signed by a key no installed copy pins is refused by every machine, unattended
or not. Keys also expire (the pinned OpenPGP ones in 2028): extend or replace them in a
release made well before that date, because a copy that never received the
extended key sees the signature as expired and treats it as unverifiable.

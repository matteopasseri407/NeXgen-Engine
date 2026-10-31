# Security

🇮🇹 [Italiano](SECURITY.it.md)

## Reporting a vulnerability

Open a private security advisory from this repository's Security tab, or email the address on the maintainer's GitHub profile.
Use a private report when credentials are exposed or an exploit is live.
Other bugs can go in a public issue.

## What must never be committed

- Anything under `99-SECRETS/` except `README.md`, `.gitkeep` and `secrets-registry.md`. `.gitignore` blocks the rest already, so check before you force-add a file in that folder.
- Real API keys, tokens, SSH keys, webhook secrets or tunnel credentials in any file, including MCP manifests, `.env` files and example configs. Files matching `03-INFRA/deploy/*/.env.example` hold placeholders only.
- Personal data about you or someone else: real names tied to private context, private chat IDs, internal project numbers, customer data. If you fork this repository for your own use, keep that material out of the parts you push publicly.

If one of these is already committed, treat it as a leak: rotate the credential first, then clean the history, not only the latest commit.

## Trust boundaries

- The Vault is plain files. Any agent CLI with filesystem access can read and write all of it, and your OS permissions are the only per-file control. If more than one person would share a Cloud-Server backend, `docs/org-deployment.md` describes what that means in practice.
- `nexgen sync` and `nexgen doctor` (the old `agent-sync` and `agent-doctor` names still work) run with your user's permissions. They read and patch CLI config files (see `docs/what-gets-written.md`) and, in the MULTI profile, install a systemd user timer on Linux or a Task Scheduler entry on Windows. Neither uses sudo. The server role is the exception: it installs the agents' sudo policy at `/etc/sudoers.d/nexgen-agents`, a fixed list of fifteen commands, and `docs/roles.md` describes it.
- MCP servers run as local processes or connect to your own VPS. No tool in the default manifest sends Vault content to a third-party model or SaaS API in normal operation. Semantic search, OCR and scraping are self-hosted on infrastructure you deploy (`03-INFRA/deploy/`), not a service this project runs for you. A hosted MCP server you add yourself brings its own privacy and security terms.
- The browser MCP attaches to a real, visible Chrome window over the DevTools protocol. An agent that launches a headless browser behind your back is a bug.
- Cloud-Server mode reaches your VPS over an SSH tunnel you configure. Tunnel ports and credentials live in your own `99-INDEX/USER-PROFILE.md` and `99-SECRETS/`, not in this repository.

## Two leak scans, two audiences

The engine ships one secret-detection module, `03-INFRA/agent-universal-layer/leak-scan/leak_scan.py`, behind two gates with different scopes:

- The Council scan protects the end user and is always on. Every `council.py` call scans the outgoing brief, and the text a seat sends back, before either reaches or returns from a third-party model. It runs for every user in every session, with no opt-out short of not using Council. See the Guardrails section of `docs/council.md`.
- The CI leak scan is the repository's publishing gate. It runs on every pull request and keeps likely secrets and personal paths out of the public history. Normal users never publish engine code, so it has nothing to do with their Vault or their Council sessions.

GitHub branch protection is the enforcement boundary: `main` requires passing CI and signed commits, and refuses force pushes and branch deletion. It does not require an approving review, because a single maintainer cannot approve their own pull request, and a rule nobody can satisfy gets switched off. Local developer conveniences are not security controls and do not ship with the product.

## Supported versions

There is no formal LTS or patch schedule yet. Security fixes land on `main`, and no older release branch receives backports.

## Release signatures

Every commit on `main` from `8fcd351` (2026-07-08) onward carries a verifiable signature (`git verify-commit`). Release tags are made with `git tag -s` on a maintainer machine and checked with `git verify-tag`; both OpenPGP and SSH formats are accepted.

`nexgen release ship` signs with the key the machine's git is configured to use and, before pushing, verifies the tag against the trust anchor installed on that machine, so a tag the fleet would refuse is deleted locally and never published. See [docs/agent-lanes.md](docs/agent-lanes.md#releasing).

The release workflow accepts an annotated tag object only, so a lightweight tag is refused before anything is published. It then verifies the tag with the trust anchor and the verifier of the previous release, refuses a tag whose commit is not on `main`, and refuses a commit that has not passed the checks `main` requires. The first release after the anchor appeared has no predecessor and is verified against its own tree; the workflow log says so. To run the same check yourself before pushing a tag:

```bash
python3 03-INFRA/scripts/nexgen_core/release_trust.py vX.Y.Z --trust-dir <trust/ of the previous release>
```

It exits non-zero when installed copies would refuse the tag.

### Older tags

Earlier tags are not a uniform baseline, and this file previously claimed they were. Check before you trust one:

- `v0.1.0` to `v0.3.0` predate the signing discipline and are unsigned.
- `v0.5.0` and `v0.5.1` are unsigned; `v0.5.2` is a lightweight tag, not a tag object.
- `v0.93.0` to `v0.97.6` are unsigned. From `v0.93.0` the release workflow created the tag on a CI runner, which holds no signing key, and `--verify-tag` only confirms that a tag exists. Twelve releases shipped that way before the gap was found on 2026-08-06.
- Everything else from `v0.3.1` onward is signed.

An unsigned tag after `v0.3.0` violates the release policy.

### What an installed copy verifies

`nexgen-update` verifies the tag object of the release it is about to install, not the commit the tag points at. For a release, that commit is a merge made by GitHub, whose signature says nothing about the maintainer. The check uses the signers pinned in `03-INFRA/agent-universal-layer/trust/` of the copy that is already installed, never of the release being installed: a release cannot vouch for itself. OpenPGP checks run in a throwaway keyring holding only the pinned keys, so the machine's own keyring is neither trusted nor changed.

| Outcome | Meaning | Interactive | `--unattended` |
| --- | --- | --- | --- |
| verified | signed by a pinned key | installs | installs |
| bad | the signature does not match the tag, or its key is revoked | refused | refused |
| untrusted | a valid signature, by a key the install does not pin | refused | refused |
| unverifiable | unsigned or lightweight tag, no anchor installed yet, `gpg`/`ssh-keygen` missing, pinned key expired | warns, you decide | refused |

`--check` reports the same verdict and refuses `bad` and `untrusted` too.

Pinned signers today, the keys that signed the `v2.x` releases:

- OpenPGP `5D06 1CE9 626C 9CC8 BD88  761F 4399 A81E 895E B96F`
- OpenPGP `6CD1 92BE 0A78 7F77 867F  3B9F 9115 22B8 F02F FA88`
- SSH `SHA256:zCkqYfvAjmrD+kCDQ9Jp0plP8fV89mM+GfavKOmGvsA`

### What this does not cover

- A copy installed before `trust/` existed has no anchor. The update that first brings `trust/` is still judged by the updater that was installed, which only warned, so the guarantee starts with the update after it.
- A compromised maintainer key or maintainer machine. The anchor decides whose signature counts; it cannot tell whether the signer was coerced or breached.
- PyPI publication, when `PYPI_API_TOKEN` is set, uses a long-lived API token. Trusted publishing with OIDC needs the publisher registered on pypi.org first and is not enabled.

## Changing a signer

Changing a signer takes two releases: ship the new key in a release signed by a key that is already pinned, and only then sign with the new one. A release signed by a key that no installed copy pins is refused on every machine, unattended or not.

Keys also expire, and the pinned OpenPGP ones do in 2028. Extend or replace them in a release made well before that date, because a copy that never received the extended key reads the signature as expired and treats it as unverifiable.

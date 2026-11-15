# Distribution channels (release packages)

🇮🇹 [Italiano](it/release-packages.md)

What a tagged, signed release publishes, what runs on its own, and what needs a maintainer decision. Every claim below
is enforced by a workflow step or stated as a gap.

## Published on every release (no secrets needed)

Every tag produces three assets:

- `nexgen_engine-X.Y.Z.tar.gz`, the sdist that pipx and uv install
- `nexgen_engine-X.Y.Z-py3-none-any.whl`, the wheel
- `SHA256SUMS`, the sha256 of every asset, so any installer can verify

These are the only byte source for the downstream channels: the Homebrew formula points at the sdist asset and carries
its sha256, and PyPI receives the same files. One artifact, one hash, verified everywhere.

## Homebrew tap (automatic, needs one-time setup)

The workflow step "Update the Homebrew tap" runs on every release and pushes a generated `Formula/nexgen.rb` to
`matteopasseri407/homebrew-nexgen`. It is gated on the `HOMEBREW_TAP_TOKEN` secret: until that secret exists, the step
shows as a visible skip and does not fail the release.

One-time setup:

1. Create the public repository `homebrew-nexgen` (empty is fine; it only carries generated formulae).
2. Create a fine-grained token scoped to that repository with `contents: read/write` only, and register it as the repo
   secret `HOMEBREW_TAP_TOKEN` on NeXgen-Engine.
3. The next signed release generates the formula and pushes it. The generator is
   `03-INFRA/scripts/release_packages.py`, which pins every dependency the package declares (runtime ones and the
   build backend) as a Homebrew resource, at the digest PyPI states at build time.
4. Known gap: the formula carries the declared dependencies, not their transitive closure, which Homebrew usually
   resolves with `brew update-python-resources`. Nobody has installed from the tap yet, because the workflow step is a
   skip until the token exists.

Then: `brew install matteopasseri407/nexgen/nexgen`.

## PyPI (automatic, needs one-time setup)

Register a PyPI API token as the repo secret `PYPI_API_TOKEN`, and the "Publish to PyPI" step uploads the same sdist
and wheel on every release. Until then `uv tool install nexgen-engine` resolves to nothing, which is why the README
installs from the git URL.

## Maintainer machines

Some commands publish the engine instead of using it: `nexgen release ...`, the leak gate of the engine's own
repository, the discipline hooks of that repository, and any private tool the engine launches. They run only on a
machine registered as maintainer, and a registration is two facts, neither of them in this repository:

- `maintainer: true` in the installation's private config, `sync/remotes.yaml` in the Vault, the file the
  update-channel gate already reads.
- a git signing key this machine would sign with (`git config user.signingkey`, or the key its keyring signs with),
  listed in `03-INFRA/agent-universal-layer/trust/release-signers.txt`.

`nexgen maintainer status` says which half is missing; `nexgen maintainer register` writes the flag into that private
config and leaves it uncommitted, so publishing it is a separate `nexgen vault push`. The flag on its own authorises
nothing: without a listed key the installation is not a maintainer, and every gated command says so in one line.
Everything a person needs stays outside this gate, the discipline workflow in their own projects included.

**This is not a security boundary.** What stops a stranger is GitHub's own permissions: `main` is protected and
requires the checks to pass, release tags must be signed, and the owner is the only collaborator. The registration
keeps the publisher's tools out of the way of an installation that has no use for them; a machine that set the flag
without a listed key would still have nothing to push, tag or release with.

## Windows: what exists, what is a decision

- `install.ps1 -Check` in the repo root is the Windows path today, versioned with the repo.
- Authenticode signing is a known gap: it needs a code-signing certificate (an OV one costs roughly 100 to 400 € a
  year, an EV one more) and a personal decision about which identity signs. Nothing in the repo pretends to be signed
  until that happens.
- `winget` and `scoop` manifests need a real installer artifact (a portable exe or an MSI), not a Python sdist. That
  is a packaging decision, PyInstaller single file or MSI, deliberately not made here.

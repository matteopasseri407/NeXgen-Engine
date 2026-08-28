# Distribution channels (release packages)

What a tagged, signed release publishes, what activates automatically, and
what needs a maintainer decision. The rule from SECURITY.md applies here
too: this file describes what the repo does, and every claim below is
enforced by a workflow step or stated as a gap.

## Published on every release (no secrets needed)

Since the release workflow gained a build step, every tag produces:

- `nexgen_engine-X.Y.Z.tar.gz` — the sdist (what pipx/uv install)
- `nexgen_engine-X.Y.Z-py3-none-any.whl` — the wheel
- `SHA256SUMS` — sha256 of every asset, so any installer can verify

These are the single byte source for every downstream channel: the
Homebrew formula points at the sdist asset and carries its sha256, and
PyPI (when configured) receives the same files. One artifact, one hash,
verified everywhere.

## Homebrew tap (auto-update, needs one-time setup)

The workflow step "Update the Homebrew tap" runs on every release and
pushes a generated `Formula/nexgen.rb` to `matteopasseri407/homebrew-nexgen`.
It is gated on the `HOMEBREW_TAP_TOKEN` secret: until it exists, the step
is a visible skip, not a failure.

One-time setup:

1. Create the public repository `homebrew-nexgen` (empty is fine; it only
   ever carries generated formulae).
2. Create a fine-grained PAT scoped to that repository with
   `contents: read/write` only, and register it as the repo secret
   `HOMEBREW_TAP_TOKEN` on NeXgen-Engine.
3. The next signed release generates the formula (via
   `03-INFRA/scripts/release_packages.py`, which pins the PyYAML resource
   to the digest PyPI states at build time) and pushes it.

Then: `brew install matteopasseri407/nexgen/nexgen`.

## PyPI (auto-publish, needs one-time setup)

Register a PyPI API token as the repo secret `PYPI_API_TOKEN` and the
"Publish to PyPI" step uploads the same sdist + wheel on every release.
Until then, `uv tool install nexgen-engine` resolves to nothing (the
package is not on PyPI), which is why the README's quick start installs
from the git URL.

## Windows: what exists, what is a decision

- `install.ps1 -Check` (repo root) is the Windows path today, versioned
  with the repo.
- **Authenticode signing is a declared gap**: it requires a code-signing
  certificate (OV from roughly 100-400 €/year, EV more), and a personal
  decision about which identity signs. Nothing in the repo pretends to be
  signed until that happens.
- `winget`/`scoop` manifests need an actual installer artifact (portable
  exe or MSI) rather than a Python sdist; that is a packaging decision
  (PyInstaller single-file vs MSI) deliberately not made here.

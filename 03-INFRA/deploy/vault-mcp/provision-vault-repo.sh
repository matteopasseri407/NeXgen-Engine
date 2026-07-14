#!/usr/bin/env bash
# provision-vault-repo.sh — create the Git plumbing the vault-mcp container
# mounts: a bare repo (the vault's authoritative remote on this VPS) plus a
# checked-out worktree, kept in sync by a post-receive hook.
#
# Idempotent by design: safe to re-run on every bootstrap-vps.sh invocation.
# An existing bare repo / worktree is never re-initialized or overwritten —
# re-runs only (re)install the hook and normalize ownership.
#
# Ownership normalization is not cosmetic: a single root-owned file inside
# the worktree makes the post-receive checkout fail with "unable to create
# file ... Permission denied" AFTER the push has already succeeded, leaving
# the worktree silently stale. (Observed in production 2026-07-02 when
# root-owned backup files landed in a vault worktree.)
#
# Paths match the defaults in docker-compose.yml next to this script;
# override both together via .env / environment:
#   VAULT_BARE_DIR      (default /opt/knowledge-vault.git)
#   VAULT_WORKTREE_DIR  (default /opt/knowledge-vault)
#   VAULT_BRANCH        (default main)

set -euo pipefail

BARE_DIR="${VAULT_BARE_DIR:-/opt/knowledge-vault.git}"
WORKTREE_DIR="${VAULT_WORKTREE_DIR:-/opt/knowledge-vault}"
BRANCH="${VAULT_BRANCH:-main}"
OWNER_UID="$(id -u)"
OWNER_GID="$(id -g)"
OWNER_SPEC="${OWNER_UID}:${OWNER_GID}"

command -v git >/dev/null 2>&1 || { echo "missing: git"; exit 1; }

# Escalation only where needed (default paths live under /opt): mirror the
# graceful SUDO handling of bootstrap-vps.sh — root needs none, sudo if
# present, otherwise fail with a clear message instead of a permission error.
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  fi
fi

make_owned_dir() {
  local dir="$1"
  if [ -d "$dir" ]; then
    return 0
  fi
  if mkdir -p "$dir" 2>/dev/null; then
    return 0
  fi
  if [ -n "$SUDO" ] && $SUDO mkdir -p "$dir" && $SUDO chown "$OWNER_SPEC" "$dir"; then
    return 0
  fi
  echo "cannot create $dir (no write permission and no sudo) -- create it as root:"
  echo "  mkdir -p $dir && chown $OWNER_SPEC $dir"
  exit 1
}

make_owned_dir "$BARE_DIR"
make_owned_dir "$WORKTREE_DIR"

# --- bare repo -------------------------------------------------------------
if [ ! -f "$BARE_DIR/HEAD" ]; then
  git init --bare --initial-branch="$BRANCH" "$BARE_DIR"
  echo "  initialized bare repo: $BARE_DIR (branch $BRANCH)"
else
  echo "  bare repo already present: $BARE_DIR"
fi

# --- post-receive hook -------------------------------------------------------
# (Re)written on every run so path changes in .env propagate. Checkout runs
# as the pushing user; ownership normalization below keeps that possible.
HOOK="$BARE_DIR/hooks/post-receive"
cat > "$HOOK" <<EOF
#!/usr/bin/env bash
# Installed by provision-vault-repo.sh — keeps the worktree in sync with
# every push. Failing loudly here is correct: a failed checkout means the
# worktree is stale even though the push succeeded.
set -euo pipefail
GIT_WORK_TREE="$WORKTREE_DIR" GIT_DIR="$BARE_DIR" git checkout -f "$BRANCH"
EOF
chmod +x "$HOOK"
echo "  post-receive hook installed ($HOOK)"

# --- initial checkout --------------------------------------------------------
# If the bare repo already has history (e.g. the workstation pushed before
# this script ever ran) and the worktree is still empty, populate it now.
if git --git-dir="$BARE_DIR" rev-parse --verify --quiet "$BRANCH" >/dev/null; then
  if [ -z "$(ls -A "$WORKTREE_DIR" 2>/dev/null)" ]; then
    GIT_WORK_TREE="$WORKTREE_DIR" GIT_DIR="$BARE_DIR" git checkout -f "$BRANCH"
    echo "  worktree populated from existing $BRANCH"
  fi
else
  echo "  bare repo has no commits yet -- push the vault from the workstation"
  echo "  (git remote add <name> <this-vps>:$BARE_DIR && git push <name> $BRANCH)"
fi

# --- ownership normalization -------------------------------------------------
# See header comment: any file in either tree not owned by the deploy user
# eventually breaks pushes or MCP writes. chown is a no-op when already
# correct, so this is cheap to run every time.
for dir in "$BARE_DIR" "$WORKTREE_DIR"; do
  if [ -n "$(find "$dir" ! -user "$OWNER_UID" -print -quit 2>/dev/null)" ]; then
    if [ -n "$SUDO" ] || [ "$(id -u)" -eq 0 ]; then
      $SUDO chown -R "$OWNER_SPEC" "$dir"
      $SUDO chmod -R u+rwX,g+rX "$dir"
      echo "  normalized ownership on $dir"
    else
      echo "WARNING: $dir contains files not owned by uid $OWNER_UID and sudo is"
      echo "  unavailable -- pushes/writes may fail until ownership is fixed."
    fi
  fi
done

echo "  vault repo plumbing ready: bare=$BARE_DIR worktree=$WORKTREE_DIR branch=$BRANCH"

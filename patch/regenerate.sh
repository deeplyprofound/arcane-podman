#!/usr/bin/env bash
# Regenerate the fork's patch snapshots against the current upstream tip.
#
# Each patch is just `git diff upstream/main -- <paths>` for one concern, so the
# files under patch/ always reflect exactly how this fork differs from upstream
# in the handful of upstream-owned files we edit in place. Re-run after every
# upstream sync so the recorded base + line context stay current.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Make sure we're diffing against the latest upstream (non-fatal if offline).
git fetch upstream --quiet 2>/dev/null || echo "warn: could not fetch upstream; diffing against cached upstream/main" >&2

BASE="upstream/main"
BASE_SHA="$(git rev-parse --short "$BASE")"

gen() { # gen <output-file> <path...>
  local out="$1"; shift
  git diff "$BASE" -- "$@" > "patch/$out"
  printf '  %-48s (%s bytes)\n' "$out" "$(wc -c < "patch/$out" | tr -d ' ')"
}

echo "Regenerating patches against upstream/main @ $BASE_SHA"
gen 0001-disable-release-workflow-on-fork.patch .github/workflows/release.yml
gen 0002-devcontainer-identity.patch            .devcontainer/devcontainer.json
gen 0003-podman-socket-autodetect.patch         backend/internal/config/config.go .env.example

echo "Done. Remember to update the pinned base commit in patch/README.md if it changed ($BASE_SHA)."

# `patch/` — fork maintenance

This repo is a **fork** of [`getarcaneapp/arcane`](https://github.com/getarcaneapp/arcane).
Goal: add first-class **Podman** support while tracking upstream closely.

The few changes that make this a fork (vs. upstream) are kept small, isolated,
and recorded here as patches so they survive every upstream sync and stay easy
to review.

## Guiding rule

**The enemy is merge conflicts.** So:

- **Add in new files upstream never touches** (Podman code, new docs, this
  folder) — those never conflict.
- Only edit upstream-owned files when unavoidable, keep each edit tiny, and
  record it here.

Keep the Go module path `github.com/getarcaneapp/arcane/...` **as-is** — it's in
every import; renaming it would conflict on nearly every file each sync for zero
benefit. Our identity lives in the *images we publish*, not the module path.

## Upstream base

- upstream remote: `git@github.com:getarcaneapp/arcane.git` (add with
  `git remote add upstream …` if missing)
- patches here were generated against upstream commit:

      4ff83971  (upstream/main at last sync — run `git rev-parse upstream/main` for full SHA)

## The patches

| file | touches | what / why |
|------|---------|------------|
| `0001-disable-release-workflow-on-fork.patch` | `.github/workflows/release.yml` | Gates the `release` job on `github.repository == 'getarcaneapp/arcane'` so it never runs on the fork. The fork has no goreleaser-pro key / cosign / macOS notary / Depot, and the upstream job also mints a `getarcaneapp` GitHub App token that would hard-fail here. |
| `0002-devcontainer-identity.patch` | `.devcontainer/devcontainer.json` | Renames the dev container to "Arcane-Podman Dev Container" and sets `TZ` to `America/New_York`. |
| `0003-podman-socket-autodetect.patch` | `backend/internal/config/config.go`, `.env.example` | Adds one `applyContainerHostDefaults(cfg)` call in `config.Load()` (+ `.env.example` docs) so an unset `DOCKER_HOST` auto-detects a Podman socket. The bulk of the feature lives in **new** files (`backend/pkg/libarcane/enginesocket/`, `backend/internal/config/containerhost.go`) that upstream never touches, so they carry via git history, not this patch. Upstreamable. |

### Deferred (not yet written)

- **fork-identity-quay** — swap `getarcaneapp` → our identity in
  `.goreleaser.yaml` and publish to **`quay.io/iguessipentest/arcane-podman`**.
  Deferred on purpose: `.goreleaser.yaml` is only exercised by the (currently
  gated-off) release pipeline, so patching it now just invites drift. Write it
  when we re-enable releases as a lean, image-only push to Quay (with a Quay
  robot account → GitHub secrets `QUAY_USERNAME` / `QUAY_TOKEN`).

## Known fork gotchas (not yet addressed)

- CI (`.github/workflows/ci.yml`) and the release workflow both use Depot-hosted
  runners (`depot-ubuntu-24.04-16`) and a Depot project (`depot.json` →
  `np622krb2x`) that belong to upstream. Expect CI to need attention when we
  start pushing branches. Out of scope for the initial scaffold.

## Syncing with upstream

```sh
git fetch upstream
git rebase upstream/main        # replays our commits on top (3-way merge)
# resolve any conflicts — should be tiny; the two files above are the only
# in-place edits. Then:
./patch/regenerate.sh           # refresh the .patch snapshots against the new base
# update the pinned base commit above if it changed
```

If a rebase ever gets messy, the patches are a portable fallback — start from a
clean upstream checkout and replay them onto the working tree:

```sh
git apply patch/0001-*.patch patch/0002-*.patch
```

## Regenerating

`./patch/regenerate.sh` re-exports each concern as `git diff upstream/main --
<paths>` into this folder. Run it after every sync.

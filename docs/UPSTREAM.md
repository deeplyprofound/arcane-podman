# Tracking upstream (post hard-fork)

`deeplyprofound/arcane-podman` began as a fork of
[`getarcaneapp/arcane`](https://github.com/getarcaneapp/arcane) and has since
**hard-forked**: the repo was reorganized into `clients/` + `server/` + `docs/`
+ `pipes/`, so `git rebase upstream/main` is **no longer clean** — every moved
path conflicts. This retires the old `patch/` snapshot workflow. Upstream is now
a **manual cherry-pick source**, not a rebase target.

## Remotes

```sh
git remote add upstream git@github.com:getarcaneapp/arcane.git   # if missing
git fetch upstream
```

## Pulling an upstream fix

1. Find the upstream commit(s): `git log upstream/main -- <upstream/path>`.
2. Inspect the diff: `git show <sha>`.
3. Apply by hand into the new layout, translating paths with the map below.
   `git cherry-pick <sha>` will conflict on paths; resolve by moving the hunks
   to the new locations, or `git show <sha> | git apply --3way -p1 --directory=<newroot>`
   for a single-subtree change.
4. Re-run the relevant checks (`just test`, `just typegen`, `go build`).

## Path map (upstream → this fork)

| upstream | here |
|----------|------|
| `backend/` | `server/backend/` |
| `types/` | `server/types/` |
| `backend/resources/migrations/` | `server/database/migrations/` (own Go module) |
| `cli/` | `clients/cli/` |
| `frontend/` | `clients/webapp/svelte/` |
| `tests/` | `pipes/gates/` |
| `docker/` | `pipes/deployment/docker/` |
| `CONTRIBUTING.md`, `SECURITY.md`, `AI_POLICY.md` | `docs/` |

Go **module/import paths are unchanged** (`github.com/getarcaneapp/arcane/...`),
so upstream changes to `.go` files usually apply with only the leading directory
translated. Watch for:

- **go.mod `replace` directives** — ours are relative to the new depths.
- **migrations** — upstream adds them under `backend/resources/migrations/`; here
  they live in the `server/database` module (`just db-new <name>` scaffolds the
  paired sqlite+postgres files).
- **frontend types** — we generate `clients/webapp/svelte/src/lib/types/api.ts`
  from the huma OpenAPI spec (`just typegen`); don't hand-port upstream's
  hand-written `src/lib/types` changes — regenerate instead.
- **CI** — upstream edits `.github/workflows` and `.depot/workflows`; both exist
  here with rewired paths.

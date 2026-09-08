# arcane-podman docs

Documentation for **arcane-podman** — a hard-fork of
[`getarcaneapp/arcane`](https://github.com/getarcaneapp/arcane) adding
first-class Podman support.

## Contents

- [CONTRIBUTING](CONTRIBUTING.md) — how to build, test, and contribute
- [SECURITY](SECURITY.md) — reporting vulnerabilities
- [AI_POLICY](AI_POLICY.md) — policy for AI-assisted contributions
- [UPSTREAM](UPSTREAM.md) — tracking upstream after the hard-fork (cherry-pick guide + path map)
- [CHANGELOG](CHANGELOG.md) — release history
- [LICENSE](LICENSE) — BSD-3-Clause

## Repository layout

```
server/    backend/ (Go API) · types/ (shared DTOs) · database/ (goose migrations module)
clients/   cli/ (Cobra CLI) · webapp/svelte/ (SvelteKit UI)
pipes/     gates/ (e2e/tests) · deployment/{docker,podman}/
.config/   linters (golangci, snyk, custom-gcl) + signing key
scripts/   dev/utility scripts
```

## Common tasks

- `just typegen` — regenerate `clients/webapp/svelte/src/lib/types/api.ts` from the backend's OpenAPI spec
- `just db-new <name>` / `just db-status` — author/inspect paired sqlite+postgres migrations
- See the root `Justfile` (`just --list`) for the full recipe set.

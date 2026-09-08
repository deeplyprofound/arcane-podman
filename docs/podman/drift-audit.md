# Podman drift audit

Gap analysis for running **arcane-podman** against Podman instead of Docker.
Grounded in authoritative Podman docs (*Podman in Action*, *Podman for DevOps*,
Podman v5.8.1 reference) cross-referenced against the current code. Citations
use `InAction:<line>` / `DevOps:<line>` from the source texts.

## The good news (validated)

The Docker-**compat API itself is solid**: Podman targets the Docker **v1.40**
API and implements every endpoint **except Swarm** (`InAction:8710-8712`). That
v1.40 floor exactly matches our moby client's `MinAPIVersion`, so connection +
inventory (`ContainerList`/`ImageList`/`NetworkList`/`VolumeList`/`Events`/`Ping`)
work as-is. Already-correct Podman handling in the tree:

- **Socket auto-detect** — `enginesocket.ResolveContainerHost` (Docker-first, then
  rootful/rootless Podman sockets). ✅ (shipped)
- **CIDR-address normalization** — `libarcane/inspect_compat.go` refetches raw
  daemon JSON and fixes Podman's CIDR-suffixed IP/gateway fields that the typed
  moby structs reject. ✅
- **Events `TimeNano` stamping** — `docker/service.go:288` back-fills Podman's
  missing nanosecond timestamps. ✅
- **Compose runtime over the compat socket** — docker-compose-over-REST is
  explicitly supported (`InAction:9026-9031`); our embedded `docker/compose/v5`
  up/down/ps/logs path (`pkg/projects/compose.go`) works against Podman. ✅
- **Tool images fully qualified** — `ghcr.io/getarcaneapp/tools|...` avoid
  short-name resolution. ✅

## The root cause behind most fixes

Arcane **detects** the engine (`libarcane/engine_compat.go` `normalizeEngineNameInternal`)
but uses it for exactly **one** thing (stripping `MemorySwappiness`), and only in
the self-upgrade CLI. There is no engine *capability* surfaced to the rest of the
backend or the frontend. **Fix once, unblock many:** cache engine identity
(docker|podman) + cgroup version on `DockerClientService`, expose it via the
system/info API as a capability object, and drive both **gating** (Swarm, builds,
image-patch) and **behavior** (SELinux relabel, short-name qualification, default
network, messaging) off it. Nearly every finding below hangs off this.

## Severity summary

| # | Area | Severity | One-line |
|---|------|----------|----------|
| B1 | Rootless identity | **blocker** | socket-GID group + forced UID 65532 re-exec break rootless socket access |
| B2 | cgroup-v2 sanitize | **blocker** | `PrepareRecreateHostConfigForEngine` not wired into recreate/create/edit |
| B3 | SELinux relabel | **fixed ✓** | `volumehelper.HostConfig` is engine-aware: on Podman+SELinux it relabels host-path bind strings (`:z`) and converts host-path `mount.Mount`→`:z` Binds string (the Mounts API has no relabel field). All 7 helper/backup/rustic/rename call sites wired. Verified on the enforcing Fedora CoreOS VM (below). |
| B4 | Swarm reachable | **gated ✓** | InitSwarm/JoinSwarm/LeaveSwarm/UnlockSwarm short-circuit with `ErrSwarmUnsupportedOnPodman` (4xx) on Podman. Frontend nav-hide of `/swarm/cluster` is a follow-up (needs generated type). |
| B5 | BuildKit builds | **gated ✓** | `BuildService.BuildImage` returns `ErrBuildUnsupportedOnPodman` (4xx) instead of dialing BuildKit /grpc+/session. |
| B6 | Copacetic patch | **gated ✓** | `ImagePatchService.PatchImage` returns `ErrPatchUnsupportedOnPodman` (4xx) instead of a BuildKit `docker://` dial. |
| B7 | Short-name pulls | **fixed/moot ✓** | Our bare `alpine:latest` default is now `docker.io/library/alpine:latest`. B7b (normalize USER short names) is NOT done on purpose: the compat `/images/create` already defaults unqualified names to docker.io (verified live) AND preserves explicit registries — normalizing would override the user's registries.conf. Gated. |
| D1 | Daemonless UX | degraded | socket off by default; connect errors say "Docker" |
| ~~D2~~ | Default network | **moot (gated)** | Compat API aliases the default net to `bridge` (NOT `podman`) — `IsDefaultNetwork` already matches. Book-based finding; disproved by the sim/live gate. |
| ~~D3~~ | Restore net DNS | **moot (gated)** | Same: the default comes back as `bridge`, which `rusticRestoreNetworkModeInternal` already excludes. |
| D4 | Rootless ports | degraded | `<1024` publish fails; no inbound w/o forward — raw error |
| ~~D5~~ | Restart policy | **mostly moot** | Compat API PRESERVES the policy name (`unless-stopped`→`unless-stopped`, verified live) — the book's `≡always` is a runtime/systemd mapping, not the API. Arcane's display is correct. Only the reboot-persistence caveat (`podman-restart.service`) is informational, not a code fix. |
| D6 | Healthchecks | degraded | systemd-timer driven; rootless needs lingering; `StartInterval` ignored |
| ~~D7~~ | info/system df | **moot (gated)** | Book claim (empty GitCommit/BuildTime, no Swarm/Plugins, broken df) is FALSE over the VERSIONED compat: Components[].Details has GitCommit/GoVersion/BuildTime, /info has Swarm+Plugins, and /v1.44/system/df returns the Docker shape (only UNVERSIONED /system/df is libpod-shaped). Gated by DiskUsage test. |
| D8 | Compose discovery | degraded | only matches `com.docker.compose.*`, misses `io.podman.compose.*` |
| D9 | policy.json | degraded | signature rejection returns unclassified pull error |
| D10 | Update digest | degraded | reconstructs `docker.io/library` instead of using `RepoDigests` |
| D11 | Rootless volume own | degraded | no `:U`; relies on app-level chown |
| C1-8 | cosmetic | cosmetic | Mountpoint display, anon-label, Trivy `bridge` fallback, `host.docker.internal` (tooling), CPUShares rescale, ImageSearch scope, macvlan display |

---

## Blockers (detailed)

### B1 — Rootless identity: socket-GID group + UID-65532 re-exec
Rootless Podman's socket is owned by the **invoking user** at
`$XDG_RUNTIME_DIR/podman/podman.sock`; there is no `docker` group model, and
rootful's socket is `0660 root:root` (root-only) (`DevOps:3383-3385`;
`InAction:8532-8535`). `runtime_identity.go:241-253`
(`runtimeIdentitySupplementaryGroupsInternal`) adds the socket's GID as a
supplementary group (a Docker `root:docker` convention), and the in-container
default re-execs to UID/GID **65532** (`runtime_identity.go:22-23,155-171`) —
which then cannot open a socket owned by host UID 1000 under `/run/user/1000`.
**Action:** thread the `enginesocket` classification (`SourcePodmanRootless`/
`Rootful`) into `runtime_identity`; skip the socket-GID injection for Podman and
do not force the 65532 re-exec for rootless (keep the identity owning the socket).

### B2 — cgroup-v2 HostConfig sanitizer not on the user path
`PrepareRecreateHostConfigForEngine` (`engine_compat.go:29`, strips
`MemorySwappiness` on Podman+cgroupv2) is called **only** from
`cli/upgrade/upgrade.go:476`. The user-facing recreate/create/edit builds
(`container/service.go:279,686`, edit `:931`) bypass it, so any container
carrying `MemorySwappiness` (or other v2-rejected fields) is re-sent verbatim →
Podman rejects. Rootless is cgroups-v2-only (`InAction:6333`).
**Action:** route all recreate/create/edit `HostConfig` through the sanitizer;
extend the stripped/rescaled set (see D-notes: `CPUShares`→`cpu.weight`, and
audit `PidsLimit`/`BlkioWeight`/`KernelMemory` if ever exposed). Cache engine
detection (currently `ServerVersion`+`Info` on every call).

### B3 — SELinux relabel missing on helper/backup binds
On SELinux-enforcing hosts a bind mount keeps its label; the container is denied
and **Podman starts it without error** — failure only shows at runtime
(`DevOps:6408-6414`). Fix is `:z`/`:Z` (`DevOps:6463-6472`; `InAction:4125`). All
helper containers funnel through `volumehelper.HostConfig` (`helper.go:53-67`)
which never appends a relabel option; backup/restore mounts a **host-path bind**
(`volume/mount_utils.go:143-147` → `backup.go:71-87`, used at `backup.go:487,
1333,1436,1563`, `workspace.go:1024`).
**Action:** when the engine is Podman on an enforcing host, relabel host-path
binds. Prefer **`:z`** (shared) not `:Z` — multiple distinct helpers touch the
same repo dir and `:Z`'s private MCS categories lock them out
(`DevOps:6547-6588`). Make it conditional so Docker/non-SELinux are unaffected.

### B4 — Swarm reachable and ungated
Podman has no Swarm and never will (`InAction:1794-1796`); the compat API omits
it (`InAction:8710-8712`). Most swarm read/mutate ops are already gated behind
`ensureSwarmManagerInternal`/`ensureSwarmActiveInternal` (return
`ErrSwarmNotEnabled` because Podman's `/info` reports inactive) and
`SyncSwarmEnabledState` persists `swarm.enabled=false`. **The hole:** the nav
filter still keeps `/swarm/cluster` (`navigation-config.ts:346-348`), whose
"Initialize Swarm" calls the **ungated** `SwarmInit` (`swarm.go:1357`; same for
Join/Leave/Unlock) → raw 404/501 on Podman.
**Action:** gate on detected **engine**, not swarm state: hide `/swarm/cluster`
when engine=podman and short-circuit Init/Join/Leave/Unlock with a typed
"Swarm unsupported on Podman — use Kubernetes/`kube play`" error.

### B5 — Local builds require Docker's embedded BuildKit
`podman build` uses **Buildah**; there is no BuildKit daemon, `/grpc`, or
`/session` (`InAction:3426`; `DevOps:2257`). The `local` build provider
(`build/service.go:300,137`) uses `go.getarcane.app/builds@v0.4.1`, whose both
paths are BuildKit-bound: direct `buildkitclient.New` dialing `/grpc`
(`builder_buildkit_local.go:26`) for advanced Dockerfiles, and the "classic" path
still setting `Version: BuilderBuildKit` + a `/session` dialer
(`builder_docker.go:342-352,435`). Against Podman `/grpc` fails outright and even
plain builds surface a `/session` error.
**Action:** engine-gate the BuildKit paths; either shell out to `podman
build`/Buildah or drive the compat `/build` **without** `BuilderBuildKit`/session
(classic tar-context build — no cache mounts/frontends). Compose `build:`
services inherit this (same `compose/v5` BuildKit default); stop advertising
"using BuildKit" unconditionally (`project/handler.go:375`).

### B6 — Copacetic image patching needs BuildKit
`imagepatch.go:235` sets `copaOpts.BkAddr = "docker://"` (BuildKit in the Docker
daemon) and forces `DOCKER_HOST` for copa's connhelper (`imagepatch.go:66-73`);
`Patch()` solves a BuildKit graph. Podman has no such endpoint → every patch
fails. Copa has no Buildah driver today.
**Action:** gate the imagepatch feature off + hide in UI when engine=podman
(surface "patching requires BuildKit/Docker"), until a Buildah backend exists.

### B7 — Short-name pulls don't implicitly resolve to docker.io
Docker resolves bare `alpine:latest`→`docker.io/library/alpine`; Podman resolves
unqualified names via `unqualified-search-registries` and can **prompt** or, in
`short-name-mode=enforcing`, **fail headless** (`InAction:1702-1719`;
`DevOps:3316-3321`). Arcane ships a bare **`alpine:latest`** default
(`settings/service.go:284`, pulled `project/lifecycle.go:363`) and passes all
user/compose short names raw (`image/image.go:227,232`;
`container/service.go:154,161,1276`).
**Action:** fully qualify our own default (`docker.io/library/alpine:latest`);
when engine=podman, normalize user short names to `docker.io/library/...` via
`distribution/reference` before pull (preserves Docker semantics), or document
the `registries.conf` requirement.

---

## Degraded (behavior wrong/confusing, not fatal)

- **D1 Daemonless UX** — Podman's socket is off by default and drops idle
  connections (`InAction:8515-8564`). On connect failure with no Docker socket,
  emit Podman guidance (`systemctl --user enable --now podman.socket` rootless /
  `sudo systemctl enable --now podman.socket` rootful) and keep retry/backoff
  (`docker/service.go:242-276`); don't treat one dropped connection as fatal.
- **D2 Default network — MOOT over the compat API (gated).** The books
  (`DevOps:15013,15454`) say the default netavark net is named `podman`, but
  that's the **libpod** view. Over the **Docker-compat** `/networks` endpoint
  arcane actually uses, Podman aliases it to **`bridge`** (verified live, Podman
  6.1.1, and in the simulator). `dockerutil.IsDefaultNetwork` already matches
  `bridge`, so there's nothing to fix. Guarded by
  `pkg/dockerutil/network_integration_test.go` — if a future Podman stops
  aliasing, the gate fails and we revisit. No code change.
- **D3 Restore net DNS — MOOT (same reason).** The default returns as `bridge`,
  which `rusticRestoreNetworkModeInternal` (`systembackup/service.go:1423-1433`)
  already excludes. No code change. (This is the value of a real gate: two
  book-based "blockers" dissolved on contact with the actual compat API.)
- **D4 Rootless ports** — rootless can't publish `<1024` and has no inbound
  without forwarding (slirp4netns/pasta) (`DevOps:16437-16439,16302-16319`).
  `container/service.go` passes `PortBindings` raw → unhelpful error. Detect and
  message on `HostPort<1024`.
- **D5 Restart policy** — Podman maps `unless-stopped`→`always` and doesn't
  persist restarts across reboot without `podman-restart.service`
  (`InAction:7046-7066`; `DevOps:16628-16629`). Don't present `unless-stopped` as
  distinct; note reboot-persistence caveat.
- **D6 Healthchecks** — timer-driven via transient systemd units, not a daemon
  loop; rootless needs lingering; `StartInterval` likely ignored
  (`DevOps:12134-12180`). Note in UI; treat stuck `starting` as env-dependent.
- **D7 info/system df** — Podman omits Swarm/Plugins sections and
  `version.Components[].Details` (GitCommit/GoVersion/BuildTime), and reports 0
  build cache. `system/handler.go:275,335` mines those (→ empty);
  `system/system.go:574` build-cache prune is a no-op. Guard behind engine;
  hide build-cache controls on Podman.
- **D8 Compose discovery** — `podman-compose` uses `io.podman.compose.*` labels;
  discovery keys only on `com.docker.compose.project` (`pkg/projects/cmds.go:223`)
  → externally-created podman-compose stacks are invisible. Also match the
  `io.podman.compose.*` labels.
- **D9 policy.json** — a `reject`/`signedBy` policy blocks pulls Docker would
  allow (`DevOps:3488-3504`); `image.go:526` won't classify it. Recognize and
  surface "blocked by host signature policy."
- **D10 Update digest** — `imageupdate/image_update.go:610-628,912-977` forces
  `docker.io`+`library/`; when the host resolves a short name elsewhere the
  update check compares the wrong registry. Resolve the actual reference from
  `RepoDigests` on Podman.
- **D11 Rootless volume ownership** — no `:U` (recursive chown to container UID,
  `InAction:~3999-4045`); largely covered by arcane's app-level chown
  (`workspace.go:697-744,979,992`), gap only for pre-populated restrictive
  ownership.
- **Networking helper** — `SelectDockerHostReachableNetworkMode`
  (`network_utils.go:82-138`) relies on name-DNS on the shared net and
  `EndpointSettings.DNSNames` (Podman may leave empty); prefer a user-created
  shared net / fall through.

## Cosmetic

Volume `Mountpoint` display shows Podman storage paths (`volume/listing.go:129`);
anonymous-volume label check misses `com.docker.volume.anonymous` but the
`^[a-f0-9]{64}$` regex fallback covers it (`listing.go:303-308`); Trivy net
fallback hardcodes `bridge` (`vulnerability_trivy.go:550`, prefer empty);
`host.docker.internal` in **dev/CI tooling** only (`Justfile`, gates compose) —
Podman uses `host.containers.internal`; `CPUShares` is rescaled to `cpu.weight`
on v2; `ImageSearch` scope follows host `registries.conf`; macvlan/driver strings
render fine.

## Podman-native opportunities (additive)

- **Quadlets** — v5.8.1 has a first-class `quadlet` command;
  `.container/.pod/.network/.volume` units are the native Compose replacement.
  We have only the `pipes/deployment/podman/` scaffold (one `.container` + README).
  Build a generator (project/compose → quadlet units) validated via `podman
  quadlet`.
- **Auto-update** — `io.containers.autoupdate=registry` + `podman-auto-update.timer`
  (`InAction:7452-7480`). Surface/apply the label; optionally back the "pending
  updates" badge with `podman auto-update --dry-run`.
- **`podman secret`** — file/env-mounted secrets (distinct from Swarm secrets;
  arcane has no swarm-secret code, so purely additive) — expose in container
  create.
- **Pods** — libpod pods (shared namespaces + infra container); no Docker-API
  equivalent. Short-term cosmetic: recognize `*-infra`/`pause` containers so they
  aren't shown as orphan workloads. Longer-term: pod grouping via libpod.
- **Kube** — `podman generate kube` / `kube play` as the Podman-native
  stack-deploy alternative to Swarm.

## Suggested sequencing

1. **Engine capability plumbing** (unblocks everything): cache engine+cgroup on
   `DockerClientService`, expose via system API, consume in frontend.
2. **Connectivity blockers**: B1 (rootless identity), D1 (daemonless UX).
3. **Runtime correctness**: B2 (cgroup sanitize on all paths), B3 (SELinux `:z`),
   D2/D3 (default-network recognition), D4/D5/D6 messaging.
4. **Feature gating**: B4 (Swarm), B5/B6 (builds + patch) — hide/degrade on Podman.
5. **Registry**: B7 (short-name), D9/D10.
6. **Podman-native**: quadlet generator, auto-update, pods/kube, secrets.
</content>

## Appendix: SELinux relabel — verified on the enforcing VM (B3)

Probed on the rootless Fedora CoreOS `podman machine` (SELinux Enforcing), over
the same Docker-compat API arcane uses:

```
host file label:              unconfined_u:object_r:user_home_t:s0
HostConfig.Binds ":z"      -> reads the file; label relabeled to container_file_t
HostConfig.Mounts (bind)   -> "Permission denied" (Mounts API has no relabel field)
podman run -v src:/data:ro -> Permission denied
podman run -v src:/data:ro,z -> reads the file
```

Conclusion: relabel must be expressed as a `Binds` string with `:z` (not via the
Mounts API). That is what `HostConfig` now does on Podman+SELinux.

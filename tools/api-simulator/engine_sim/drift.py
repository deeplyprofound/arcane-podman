"""Executable registry of the documented Docker<->Podman drifts.

Each Drift ties a doc citation to the concrete behavioral difference the
simulator encodes, so the two profiles are a runnable version of
docs/podman/drift-audit.md. Handlers/serializers consult these toggles; tests
assert them. Add an entry when a new drift is encoded.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Drift:
    id: str          # audit id (B1..B7, D1..D11) or a descriptive slug
    area: str
    summary: str
    docker: str      # docker-side behavior
    podman: str      # podman-side behavior
    citation: str    # doc source


REGISTRY: list[Drift] = [
    Drift(
        id="engine-identity",
        area="system",
        summary="Engine name is only reliably in /version Components[].Name",
        docker='Platform.Name="Docker Engine - Community"; Components[0].Name="Engine"',
        podman='Platform.Name is the OS ("linux/arch/fedora"); Components[0].Name="Podman Engine"',
        citation="live capture (Podman 6.1.1) + drift-audit Phase 1",
    ),
    Drift(
        id="api-version-floor",
        area="system",
        summary="Compat API version advertised via /_ping Api-Version",
        docker="Api-Version ~1.51",
        podman="Api-Version 1.44 (>= client MinAPIVersion 1.40)",
        citation="live capture; InAction:8710-8712",
    ),
    Drift(
        id="buildkit-header",
        area="build",
        summary="/_ping Buildkit-Version header presence (B5/B6)",
        docker="Buildkit-Version set (e.g. 0.16.0); embedded BuildKit /grpc + /session",
        podman="Buildkit-Version EMPTY; no BuildKit endpoint (buildah)",
        citation="live capture; drift-audit B5/B6",
    ),
    Drift(
        id="cgroup-hostconfig",
        area="container",
        summary="cgroup-v2 rejects/ignores some HostConfig fields (B2)",
        docker="honors MemorySwappiness on cgroup v1/v2 as given",
        podman="cgroup v2: MemorySwappiness dropped; CPUShares rescaled to cpu.weight",
        citation="drift-audit B2; engine_compat.go",
    ),
    Drift(
        id="rootless-identity",
        area="system",
        summary="Socket ownership (B1)",
        docker="rootful socket root:docker (0660)",
        podman="rootless socket owned by the invoking user; Info.Rootless=true; SecurityOptions has name=rootless",
        citation="live capture (SecurityOptions); drift-audit B1",
    ),
    Drift(
        id="selinux",
        area="volume",
        summary="SELinux relabel required for host binds (B3)",
        docker="typically not enforcing; binds work without :z/:Z",
        podman="Fedora/RHEL enforcing; SecurityOptions has name=selinux; host binds need :z/:Z",
        citation="live capture (SecurityOptions); DevOps:6408-6414",
    ),
    Drift(
        id="default-network",
        area="network",
        summary="Default network name over the COMPAT API (refines D2)",
        docker='bridge / host / none',
        podman='compat aliases the default netavark network to "bridge" (NOT "podman"); no host/none',
        citation="live capture /networks (compat) — corrects book claim of 'podman'",
    ),
    Drift(
        id="swarm-absent",
        area="swarm",
        summary="Swarm endpoints (B4)",
        docker="/swarm -> 503 'not a swarm manager' (feature exists)",
        podman="/swarm -> 404 (endpoint absent; Swarm unsupported)",
        citation="live capture; InAction:1794-1796",
    ),
    Drift(
        id="storage-driver",
        area="system",
        summary="Info.Driver + DockerRootDir",
        docker='Driver=overlay2; DockerRootDir=/var/lib/docker',
        podman='Driver=overlay; DockerRootDir under ~/.local/share/containers (rootless)',
        citation="live capture /info",
    ),
]

BY_ID = {d.id: d for d in REGISTRY}

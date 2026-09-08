"""Engine profiles: the per-engine behavior + serialization layered on state.

- podman: host-level responses (ping/version/info/df) served from the REAL
  captured fixtures; behavior toggles derived from them (rootless, selinux,
  cgroup v2, swarm-absent, default-network alias).
- docker: doc-grounded literals until `probe.sh docker` captures real fixtures.

A Profile also knows how to seed default networks and how create-time HostConfig
is coerced (the cgroup-v2 drift), so the simulator *behaves* like the engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import fixtures


@dataclass
class Profile:
    name: str
    ping_headers: dict[str, str]
    version: dict[str, Any]
    info: dict[str, Any]
    df: dict[str, Any]
    swarm_status: int
    default_networks: list[dict[str, Any]]
    # behavior
    strip_memory_swappiness: bool  # cgroup v2 drops it
    selinux_enforcing: bool
    rootless: bool

    def coerce_host_config(self, host_config: dict[str, Any]) -> dict[str, Any]:
        """Apply the engine's create-time HostConfig behavior (B2 drift)."""
        hc = dict(host_config or {})
        if self.strip_memory_swappiness and hc.get("MemorySwappiness") is not None:
            # Podman on cgroup v2 silently drops MemorySwappiness.
            hc["MemorySwappiness"] = None
        return hc


def _docker_default_networks() -> list[dict[str, Any]]:
    return [
        {"Name": "bridge", "Id": "docker-bridge", "Driver": "bridge", "Scope": "local"},
        {"Name": "host", "Id": "docker-host", "Driver": "host", "Scope": "local"},
        {"Name": "none", "Id": "docker-none", "Driver": "null", "Scope": "local"},
    ]


_DOCKER_PING = {
    "Api-Version": "1.51",
    "Buildkit-Version": "0.16.0",
    "Builder-Version": "2",
    "Server": "Docker/27.5.1 (linux)",
    "Ostype": "linux",
    "Docker-Experimental": "false",
}

_DOCKER_VERSION = {
    "Platform": {"Name": "Docker Engine - Community"},
    "Components": [{"Name": "Engine", "Version": "27.5.1",
                    "Details": {"ApiVersion": "1.51", "Arch": "arm64", "GitCommit": "abcdef0",
                                "GoVersion": "go1.23.4", "KernelVersion": "6.10.0",
                                "MinAPIVersion": "1.24", "Os": "linux", "BuildTime": "2026-01-01T00:00:00.000000000+00:00"}}],
    "Version": "27.5.1", "ApiVersion": "1.51", "MinAPIVersion": "1.24",
    "GitCommit": "abcdef0", "GoVersion": "go1.23.4", "Os": "linux", "Arch": "arm64",
    "KernelVersion": "6.10.0", "BuildTime": "2026-01-01T00:00:00.000000000+00:00",
}

_DOCKER_INFO = {
    "ID": "sim-docker", "Name": "docker-desktop", "ServerVersion": "27.5.1", "OSType": "linux",
    "Driver": "overlay2", "DockerRootDir": "/var/lib/docker", "CgroupVersion": "2",
    "CgroupDriver": "systemd", "NCPU": 4, "MemTotal": 4113104896, "Rootless": False,
    "SecurityOptions": ["name=seccomp,profile=builtin", "name=cgroupns"],
    "Swarm": {"NodeID": "", "LocalNodeState": "inactive", "ControlAvailable": False, "Error": "", "RemoteManagers": None},
    "Plugins": {"Volume": ["local"], "Network": ["bridge", "host", "ipvlan", "macvlan", "null", "overlay"], "Log": ["json-file", "journald", "local"]},
}


def load(name: str) -> Profile:
    if name == "podman":
        info = fixtures.load_json("podman", "info") or {}
        secopts = info.get("SecurityOptions", []) or []
        nets = fixtures.load_json("podman", "networks_list") or []
        return Profile(
            name="podman",
            ping_headers=fixtures.load_ping_headers("podman"),
            version=fixtures.load_json("podman", "version") or {},
            info=info,
            df=fixtures.load_json("podman", "df") or {},
            swarm_status=404,
            default_networks=nets,
            strip_memory_swappiness=str(info.get("CgroupVersion")) == "2",
            selinux_enforcing=any("selinux" in s for s in secopts),
            rootless=bool(info.get("Rootless")),
        )
    if name == "docker":
        return Profile(
            name="docker",
            ping_headers=dict(_DOCKER_PING),
            version=dict(_DOCKER_VERSION),
            info=dict(_DOCKER_INFO),
            df={"LayersSize": 0, "Images": [], "Containers": [], "Volumes": [], "BuildCache": []},
            swarm_status=503,
            default_networks=_docker_default_networks(),
            strip_memory_swappiness=False,  # docker honors it
            selinux_enforcing=False,
            rootless=False,
        )
    raise ValueError(f"unknown profile: {name}")

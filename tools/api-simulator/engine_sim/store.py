"""The stateful fake engine: SQLAlchemy-backed CRUD + lifecycle + serialization.

Every operation mutates real DB state and (for containers/volumes/networks)
emits events, so the simulator behaves like an engine — create shows up in
list, start flips State to running, stop sets ExitCode, remove deletes. Profile
serializers apply per-engine drift on the way out.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from . import models
from .db import make_sessionmaker, session_scope
from .profiles import Profile


def _hexid() -> str:
    return secrets.token_hex(32)


def _normalize_image_ref(name: str) -> str:
    """Docker/Podman-compat unqualified-name resolution: a single-segment name
    -> docker.io/library/<name>; a registry-less multi-segment -> docker.io/…;
    anything with a registry host (dot/colon/localhost in the first segment) is
    left as-is (respects the user's registries.conf intent)."""
    parts = name.split("/")
    if len(parts) == 1:
        return "docker.io/library/" + name
    first = parts[0]
    if "." in first or ":" in first or first == "localhost":
        return name
    return "docker.io/" + name


def _now() -> tuple[str, int, int]:
    dt = datetime.now(timezone.utc)
    iso = dt.strftime("%Y-%m-%dT%H:%M:%S.%f000Z")
    return iso, int(dt.timestamp()), int(dt.timestamp() * 1e9)


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Store:
    def __init__(self, profile: Profile, db_url: str = "sqlite+pysqlite:///:memory:"):
        self.profile = profile
        self.maker: sessionmaker[Session] = make_sessionmaker(db_url)
        self._seed()

    # ------------------------------------------------------------------ seed
    def _seed(self) -> None:
        with session_scope(self.maker) as s:
            for net in self.profile.default_networks:
                s.add(models.Network(
                    id=net.get("Id") or _hexid(), name=net["Name"],
                    driver=net.get("Driver", "bridge"), scope=net.get("Scope", "local"),
                    ipam=net.get("IPAM", {}) or {}, options=net.get("Options", {}) or {},
                    labels=net.get("Labels", {}) or {},
                ))
            # A base image so image-dependent flows work out of the box.
            iso, _, _ = _now()
            s.add(models.Image(id="sha256:" + _hexid(), repo_tags=["alpine:latest"],
                               repo_digests=[], size=8000000, created=iso, config={}))

    def _emit(self, s: Session, typ: str, action: str, actor_id: str, attrs: dict[str, str]) -> None:
        _, t, tn = _now()
        s.add(models.Event(type=typ, action=action, actor_id=actor_id, actor_attributes=attrs, time=t, time_nano=tn))

    # -------------------------------------------------------------- system
    def ping_headers(self) -> dict[str, str]:
        return self.profile.ping_headers

    def version(self) -> dict[str, Any]:
        return self.profile.version

    def info(self) -> dict[str, Any]:
        info = dict(self.profile.info)
        with session_scope(self.maker) as s:
            info["Containers"] = s.query(models.Container).count()
            info["ContainersRunning"] = s.query(models.Container).filter_by(state="running").count()
            info["Images"] = s.query(models.Image).count()
        return info

    def df(self) -> dict[str, Any]:
        return self.profile.df or {"LayersSize": 0, "Images": [], "Containers": [], "Volumes": [], "BuildCache": []}

    def swarm_status(self) -> int:
        return self.profile.swarm_status

    # ------------------------------------------------------------ images
    def pull_image(self, from_image: str, tag: str) -> str:
        """Model the compat /images/create pull, including Docker-style
        unqualified-name defaulting to docker.io/library (verified: Podman's
        compat API does this too, so clients need NOT pre-qualify short names).
        """
        repo = _normalize_image_ref(from_image)
        ref = f"{repo}:{tag or 'latest'}"
        with session_scope(self.maker) as s:
            existing = s.scalars(select(models.Image)).all()
            for i in existing:
                if ref in (i.repo_tags or []):
                    return ref
            iso, _, _ = _now()
            s.add(models.Image(id="sha256:" + _hexid(), repo_tags=[ref], repo_digests=[], size=8000000, created=iso))
            self._emit(s, "image", "pull", ref, {})
        return ref

    def list_images(self) -> list[dict[str, Any]]:
        with session_scope(self.maker) as s:
            return [self._image_json(i) for i in s.scalars(select(models.Image))]

    def _image_json(self, i: models.Image) -> dict[str, Any]:
        _, created, _ = _now()
        return {"Id": i.id, "RepoTags": i.repo_tags or [], "RepoDigests": i.repo_digests or [],
                "Created": created, "Size": i.size, "Labels": i.labels or {}, "ParentId": ""}

    # ----------------------------------------------------------- volumes
    def create_volume(self, body: dict[str, Any]) -> dict[str, Any]:
        name = body.get("Name") or _hexid()[:24]
        iso, _, _ = _now()
        mount = ("/var/home/core/.local/share/containers/storage/volumes"
                 if self.profile.rootless else "/var/lib/containers/storage/volumes")
        with session_scope(self.maker) as s:
            v = models.Volume(name=name, driver=body.get("Driver", "local"),
                              mountpoint=f"{mount}/{name}/_data", created=iso,
                              labels=body.get("Labels", {}) or {}, options=body.get("DriverOpts", {}) or {})
            s.add(v)
            self._emit(s, "volume", "create", name, {})
            return self._volume_json(v)

    def list_volumes(self) -> dict[str, Any]:
        with session_scope(self.maker) as s:
            return {"Volumes": [self._volume_json(v) for v in s.scalars(select(models.Volume))], "Warnings": []}

    def inspect_volume(self, name: str) -> dict[str, Any]:
        with session_scope(self.maker) as s:
            v = s.get(models.Volume, name)
            if not v:
                raise NotFound(name)
            return self._volume_json(v)

    def remove_volume(self, name: str) -> None:
        with session_scope(self.maker) as s:
            v = s.get(models.Volume, name)
            if not v:
                raise NotFound(name)
            s.delete(v)
            self._emit(s, "volume", "destroy", name, {})

    def _volume_json(self, v: models.Volume) -> dict[str, Any]:
        return {"Name": v.name, "Driver": v.driver, "Mountpoint": v.mountpoint,
                "CreatedAt": v.created, "Labels": v.labels or {}, "Options": v.options or {},
                "Scope": v.scope, "Mounts": []}

    # ---------------------------------------------------------- networks
    def create_network(self, body: dict[str, Any]) -> dict[str, Any]:
        nid = _hexid()
        with session_scope(self.maker) as s:
            n = models.Network(id=nid, name=body["Name"], driver=body.get("Driver", "bridge"),
                               scope="local", ipam=body.get("IPAM", {}) or {},
                               options=body.get("Options", {}) or {}, labels=body.get("Labels", {}) or {},
                               internal=bool(body.get("Internal", False)), enable_ipv6=bool(body.get("EnableIPv6", False)))
            s.add(n)
            self._emit(s, "network", "create", nid, {"name": body["Name"]})
            return {"Id": nid, "Warning": ""}

    def list_networks(self) -> list[dict[str, Any]]:
        with session_scope(self.maker) as s:
            return [self._network_json(n) for n in s.scalars(select(models.Network))]

    def inspect_network(self, ref: str) -> dict[str, Any]:
        with session_scope(self.maker) as s:
            n = s.get(models.Network, ref) or s.scalars(select(models.Network).filter_by(name=ref)).first()
            if not n:
                raise NotFound(ref)
            return self._network_json(n)

    def remove_network(self, ref: str) -> None:
        with session_scope(self.maker) as s:
            n = s.get(models.Network, ref) or s.scalars(select(models.Network).filter_by(name=ref)).first()
            if not n:
                raise NotFound(ref)
            s.delete(n)
            self._emit(s, "network", "destroy", n.id, {"name": n.name})

    def _network_json(self, n: models.Network) -> dict[str, Any]:
        return {"Id": n.id, "Name": n.name, "Driver": n.driver, "Scope": n.scope,
                "EnableIPv6": n.enable_ipv6, "IPAM": n.ipam or {"Driver": "default", "Config": []},
                "Options": n.options or {}, "Labels": n.labels or {}, "Internal": n.internal,
                "Containers": {}, "Created": n.created or "0001-01-01T00:00:00Z"}

    # -------------------------------------------------------- containers
    def create_container(self, name: str | None, body: dict[str, Any]) -> dict[str, Any]:
        cid = _hexid()
        iso, _, _ = _now()
        host_config = self.profile.coerce_host_config(body.get("HostConfig", {}) or {})
        cname = name or f"sim_{cid[:12]}"
        mounts = self._mounts_from_binds(host_config.get("Binds", []) or [])
        with session_scope(self.maker) as s:
            c = models.Container(
                id=cid, name=cname, image=body.get("Image", ""), image_id="sha256:" + _hexid(),
                command=body.get("Cmd", []) or [], state="created", created=iso,
                labels=(body.get("Labels", {}) or {}), config={"Image": body.get("Image", ""), "Cmd": body.get("Cmd", []), "Labels": body.get("Labels", {}) or {}, "Env": body.get("Env", [])},
                host_config=host_config, mounts=mounts,
                network_settings={"Networks": {"bridge": {"IPAddress": "10.88.0.2", "Gateway": "10.88.0.1"}}},
            )
            s.add(c)
            self._emit(s, "container", "create", cid, {"image": body.get("Image", ""), "name": cname})
            return {"Id": cid, "Warnings": []}

    def _mounts_from_binds(self, binds: list[str]) -> list[dict[str, Any]]:
        out = []
        for b in binds:
            parts = b.split(":")
            src, dst = parts[0], (parts[1] if len(parts) > 1 else parts[0])
            typ = "volume" if "/" not in src else "bind"
            out.append({"Type": typ, "Source": src, "Destination": dst, "Mode": (parts[2] if len(parts) > 2 else ""), "RW": True})
        return out

    def _get_container(self, s: Session, ref: str) -> models.Container:
        c = s.get(models.Container, ref)
        if not c:
            c = s.scalars(select(models.Container).filter_by(name=ref)).first() \
                or s.scalars(select(models.Container).filter_by(name="/" + ref)).first()
        if not c:
            # prefix match on id (Docker allows short ids)
            c = s.scalars(select(models.Container).where(models.Container.id.like(ref + "%"))).first()
        if not c:
            raise NotFound(ref)
        return c

    def start_container(self, ref: str) -> None:
        iso, _, _ = _now()
        with session_scope(self.maker) as s:
            c = self._get_container(s, ref)
            # D4: rootless engines cannot bind privileged host ports (<1024).
            # The failure surfaces at START (the port bind), not create — model that.
            if self.profile.rootless:
                for spec in (c.host_config.get("PortBindings") or {}).values():
                    for binding in spec or []:
                        hp = str(binding.get("HostPort", ""))
                        if hp.isdigit() and int(hp) < 1024:
                            raise Conflict(f"rootless containers cannot publish privileged port {hp} (<1024); use a high port or set net.ipv4.ip_unprivileged_port_start")
            c.state, c.started_at = "running", iso
            self._emit(s, "container", "start", c.id, {"image": c.image, "name": c.name})

    def stop_container(self, ref: str) -> None:
        iso, _, _ = _now()
        with session_scope(self.maker) as s:
            c = self._get_container(s, ref)
            c.state, c.exit_code, c.finished_at = "exited", 0, iso
            self._emit(s, "container", "die", c.id, {"image": c.image, "name": c.name, "exitCode": "0"})

    def remove_container(self, ref: str) -> None:
        with session_scope(self.maker) as s:
            c = self._get_container(s, ref)
            cid = c.id
            s.delete(c)
            self._emit(s, "container", "destroy", cid, {})

    def list_containers(self, all_: bool) -> list[dict[str, Any]]:
        with session_scope(self.maker) as s:
            q = select(models.Container)
            if not all_:
                q = q.filter_by(state="running")
            return [self._container_list_json(c) for c in s.scalars(q)]

    def inspect_container(self, ref: str) -> dict[str, Any]:
        with session_scope(self.maker) as s:
            return self._container_inspect_json(self._get_container(s, ref))

    def _state_obj(self, c: models.Container) -> dict[str, Any]:
        running = c.state in ("running", "paused")
        return {"Status": c.state, "Running": running, "Paused": c.state == "paused", "Restarting": False,
                "OOMKilled": False, "Dead": False, "Pid": 4242 if running else 0,
                "ExitCode": c.exit_code, "Error": "",
                "StartedAt": c.started_at or "0001-01-01T00:00:00Z",
                "FinishedAt": c.finished_at or "0001-01-01T00:00:00Z"}

    def _container_inspect_json(self, c: models.Container) -> dict[str, Any]:
        return {"Id": c.id, "Name": "/" + c.name, "Created": c.created, "Image": c.image_id,
                "State": self._state_obj(c), "Config": c.config, "HostConfig": c.host_config,
                "Mounts": c.mounts, "NetworkSettings": c.network_settings,
                "Path": (c.command[0] if c.command else ""), "Args": c.command[1:] if c.command else []}

    def _container_list_json(self, c: models.Container) -> dict[str, Any]:
        _, created, _ = _now()
        return {"Id": c.id, "Names": ["/" + c.name], "Image": c.image, "ImageID": c.image_id,
                "Command": " ".join(c.command), "Created": created, "State": c.state,
                "Status": ("Up" if c.state == "running" else "Exited (0)"), "Ports": [],
                "Labels": c.labels or {}, "Mounts": c.mounts,
                "NetworkSettings": c.network_settings}

    def wait_container(self, ref: str) -> dict[str, Any]:
        with session_scope(self.maker) as s:
            c = self._get_container(s, ref)
            return {"StatusCode": c.exit_code}

    def buildkit(self) -> bool:
        return self.profile.buildkit

    # -------------------------------------------------------------- exec
    def create_exec(self, ref: str, body: dict[str, Any]) -> dict[str, Any]:
        with session_scope(self.maker) as s:
            c = self._get_container(s, ref)
            eid = _hexid()
            s.add(models.ExecInstance(id=eid, container_id=c.id, command=body.get("Cmd", []) or []))
            return {"Id": eid}

    def inspect_exec(self, eid: str) -> dict[str, Any]:
        with session_scope(self.maker) as s:
            e = s.get(models.ExecInstance, eid)
            if not e:
                raise NotFound(eid)
            return {"ID": e.id, "ContainerID": e.container_id, "Running": e.running,
                    "ExitCode": e.exit_code, "ProcessConfig": {"entrypoint": (e.command[0] if e.command else ""), "arguments": e.command[1:] if e.command else []}}

    # --------------------------------------------- container lifecycle (extended)
    def rename_container(self, ref: str, new_name: str) -> None:
        with session_scope(self.maker) as s:
            c = self._get_container(s, ref)
            c.name = new_name.lstrip("/")
            self._emit(s, "container", "rename", c.id, {"name": c.name})

    def restart_container(self, ref: str) -> None:
        iso, _, _ = _now()
        with session_scope(self.maker) as s:
            c = self._get_container(s, ref)
            c.state, c.started_at = "running", iso
            self._emit(s, "container", "restart", c.id, {"image": c.image, "name": c.name})

    def pause_container(self, ref: str) -> None:
        with session_scope(self.maker) as s:
            c = self._get_container(s, ref)
            if c.state != "running":
                raise Conflict(f"container {c.name} is not running")
            c.state = "paused"
            self._emit(s, "container", "pause", c.id, {"name": c.name})

    def unpause_container(self, ref: str) -> None:
        with session_scope(self.maker) as s:
            c = self._get_container(s, ref)
            c.state = "running"
            self._emit(s, "container", "unpause", c.id, {"name": c.name})

    def kill_container(self, ref: str) -> None:
        iso, _, _ = _now()
        with session_scope(self.maker) as s:
            c = self._get_container(s, ref)
            c.state, c.exit_code, c.finished_at = "exited", 137, iso
            self._emit(s, "container", "kill", c.id, {"name": c.name, "signal": "9"})
            self._emit(s, "container", "die", c.id, {"name": c.name, "exitCode": "137"})

    def prune_containers(self) -> dict[str, Any]:
        removed = []
        with session_scope(self.maker) as s:
            for c in list(s.scalars(select(models.Container).filter_by(state="exited"))):
                removed.append(c.id)
                s.delete(c)
        return {"ContainersDeleted": removed, "SpaceReclaimed": 0}

    def container_stats(self, ref: str) -> dict[str, Any]:
        # One-shot, non-streaming stats snapshot in the Docker shape.
        with session_scope(self.maker) as s:
            c = self._get_container(s, ref)
            return {"id": c.id, "name": "/" + c.name, "read": _now()[0],
                    "cpu_stats": {"cpu_usage": {"total_usage": 1000000}, "system_cpu_usage": 100000000, "online_cpus": 4},
                    "precpu_stats": {"cpu_usage": {"total_usage": 900000}, "system_cpu_usage": 99000000},
                    "memory_stats": {"usage": 8388608, "limit": 4113104896},
                    "networks": {"eth0": {"rx_bytes": 1024, "tx_bytes": 2048}}}

    # ---------------------------------------------------------- images (extended)
    def inspect_image(self, ref: str) -> dict[str, Any]:
        with session_scope(self.maker) as s:
            img = self._find_image(s, ref)
            if not img:
                raise NotFound(ref)
            return {"Id": img.id, "RepoTags": img.repo_tags or [], "RepoDigests": img.repo_digests or [],
                    "Created": img.created, "Size": img.size, "Config": img.config or {},
                    "Architecture": "arm64", "Os": "linux", "GraphDriver": {"Name": "overlay"}}

    def image_history(self, ref: str) -> list[dict[str, Any]]:
        with session_scope(self.maker) as s:
            img = self._find_image(s, ref)
            if not img:
                raise NotFound(ref)
            _, created, _ = _now()
            return [{"Id": img.id, "Created": created, "CreatedBy": "/bin/sh -c #(nop) CMD", "Size": img.size, "Tags": img.repo_tags or [], "Comment": ""}]

    def tag_image(self, ref: str, repo: str, tag: str) -> None:
        with session_scope(self.maker) as s:
            img = self._find_image(s, ref)
            if not img:
                raise NotFound(ref)
            new_tag = f"{_normalize_image_ref(repo)}:{tag or 'latest'}"
            tags = list(img.repo_tags or [])
            if new_tag not in tags:
                tags.append(new_tag)
            img.repo_tags = tags

    def remove_image(self, ref: str) -> list[dict[str, str]]:
        with session_scope(self.maker) as s:
            img = self._find_image(s, ref)
            if not img:
                raise NotFound(ref)
            iid = img.id
            s.delete(img)
            self._emit(s, "image", "delete", iid, {})
            return [{"Untagged": ref}, {"Deleted": iid}]

    def prune_images(self) -> dict[str, Any]:
        return {"ImagesDeleted": [], "SpaceReclaimed": 0}

    def _find_image(self, s: Session, ref: str) -> models.Image | None:
        img = s.get(models.Image, ref)
        if img:
            return img
        want = ref if ":" in ref.rsplit("/", 1)[-1] else ref + ":latest"
        want_norm = _normalize_image_ref(want.rsplit(":", 1)[0]) + ":" + want.rsplit(":", 1)[1]
        for i in s.scalars(select(models.Image)):
            tags = i.repo_tags or []
            if ref in tags or want in tags or want_norm in tags:
                return i
        return None

    # -------------------------------------------------------- volumes (extended)
    def prune_volumes(self) -> dict[str, Any]:
        return {"VolumesDeleted": [], "SpaceReclaimed": 0}

    # ------------------------------------------------------- networks (extended)
    def connect_network(self, ref: str, container_ref: str) -> None:
        with session_scope(self.maker) as s:
            if not (s.get(models.Network, ref) or s.scalars(select(models.Network).filter_by(name=ref)).first()):
                raise NotFound(ref)
            self._emit(s, "network", "connect", ref, {"container": container_ref})

    def disconnect_network(self, ref: str, container_ref: str) -> None:
        with session_scope(self.maker) as s:
            if not (s.get(models.Network, ref) or s.scalars(select(models.Network).filter_by(name=ref)).first()):
                raise NotFound(ref)
            self._emit(s, "network", "disconnect", ref, {"container": container_ref})

    def prune_networks(self) -> dict[str, Any]:
        return {"NetworksDeleted": []}

    # ------------------------------------------------------------ exec (extended)
    def start_exec(self, eid: str) -> None:
        with session_scope(self.maker) as s:
            e = s.get(models.ExecInstance, eid)
            if not e:
                raise NotFound(eid)
            e.running = False
            e.exit_code = 0

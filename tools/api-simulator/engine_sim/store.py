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


def _now() -> tuple[str, int, int]:
    dt = datetime.now(timezone.utc)
    iso = dt.strftime("%Y-%m-%dT%H:%M:%S.%f000Z")
    return iso, int(dt.timestamp()), int(dt.timestamp() * 1e9)


class NotFound(Exception):
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
        running = c.state == "running"
        return {"Status": c.state, "Running": running, "Paused": False, "Restarting": False,
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

"""SQLAlchemy ORM models = the engine's state tables.

These model the engine objects arcane manages. Profile serializers turn them
into Docker-API JSON (with per-engine drift applied); they are NOT the wire
shape themselves. JSON-ish fields (cmd, labels, host_config) are stored as JSON.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Container(Base):
    __tablename__ = "containers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String, index=True)
    image: Mapped[str] = mapped_column(String)
    image_id: Mapped[str] = mapped_column(String, default="")
    command: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Lifecycle: created -> running -> exited (Docker/Podman State.Status values).
    state: Mapped[str] = mapped_column(String, default="created", index=True)
    exit_code: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[str] = mapped_column(String, default="")
    started_at: Mapped[str] = mapped_column(String, default="")
    finished_at: Mapped[str] = mapped_column(String, default="")
    labels: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    host_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    network_settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    mounts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class Image(Base):
    __tablename__ = "images"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    repo_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    repo_digests: Mapped[list[str]] = mapped_column(JSON, default=list)
    size: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[str] = mapped_column(String, default="")
    labels: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Volume(Base):
    __tablename__ = "volumes"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    driver: Mapped[str] = mapped_column(String, default="local")
    mountpoint: Mapped[str] = mapped_column(String, default="")
    created: Mapped[str] = mapped_column(String, default="")
    labels: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    options: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    scope: Mapped[str] = mapped_column(String, default="local")


class Network(Base):
    __tablename__ = "networks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String, index=True)
    driver: Mapped[str] = mapped_column(String, default="bridge")
    scope: Mapped[str] = mapped_column(String, default="local")
    created: Mapped[str] = mapped_column(String, default="")
    enable_ipv6: Mapped[bool] = mapped_column(default=False)
    ipam: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    options: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    labels: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    internal: Mapped[bool] = mapped_column(default=False)


class ExecInstance(Base):
    __tablename__ = "execs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    container_id: Mapped[str] = mapped_column(String(64), index=True)
    command: Mapped[list[str]] = mapped_column(JSON, default=list)
    running: Mapped[bool] = mapped_column(default=False)
    exit_code: Mapped[int] = mapped_column(Integer, default=0)


class Event(Base):
    __tablename__ = "events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String)  # container|image|volume|network
    action: Mapped[str] = mapped_column(String)  # create|start|die|destroy|…
    actor_id: Mapped[str] = mapped_column(String, default="")
    actor_attributes: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    time: Mapped[int] = mapped_column(Integer, default=0)
    time_nano: Mapped[int] = mapped_column(Integer, default=0)

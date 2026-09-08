"""FastAPI app wiring the store to the Docker-compatible HTTP surface."""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from . import profiles
from .store import NotFound, Store

_VERSION_PREFIX = re.compile(r"^/v\d+(\.\d+)?(?=/)")


def build_app(profile_name: str, db_url: str = "sqlite+pysqlite:///:memory:") -> FastAPI:
    profile = profiles.load(profile_name)
    store = Store(profile, db_url=db_url)
    app = FastAPI(title=f"engine-sim ({profile_name})")
    app.state.store = store
    app.state.profile = profile

    @app.middleware("http")
    async def strip_version(request: Request, call_next):
        path = request.scope["path"]
        stripped = _VERSION_PREFIX.sub("", path, count=1)
        if stripped != path:
            request.scope["path"] = stripped or "/"
        return await call_next(request)

    def nf(ref: str) -> JSONResponse:
        return JSONResponse({"message": f"no such resource: {ref}"}, status_code=404)

    async def body(request: Request) -> dict[str, Any]:
        raw = await request.body()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    # ---- system ----
    @app.api_route("/_ping", methods=["GET", "HEAD"])
    def ping():
        return PlainTextResponse("OK", headers=store.ping_headers())

    @app.get("/version")
    def version():
        return JSONResponse(store.version())

    @app.get("/info")
    def info():
        return JSONResponse(store.info())

    @app.get("/system/df")
    def df():
        return JSONResponse(store.df())

    @app.get("/swarm")
    def swarm():
        code = store.swarm_status()
        msg = "page not found" if code == 404 else "This node is not a swarm manager."
        return JSONResponse({"message": msg}, status_code=code)

    @app.get("/events")
    def events(request: Request):
        # Minimal, non-following snapshot stream of recorded events (NDJSON),
        # matching Docker's line-delimited JSON event shape.
        from . import models
        from .db import session_scope

        def gen():
            with session_scope(store.maker) as s:
                for e in s.query(models.Event).order_by(models.Event.seq):
                    yield json.dumps({
                        "Type": e.type, "Action": e.action,
                        "Actor": {"ID": e.actor_id, "Attributes": e.actor_attributes or {}},
                        "scope": "local", "time": e.time, "timeNano": e.time_nano,
                    }) + "\n"

        return StreamingResponse(gen(), media_type="application/json")

    # ---- images ----
    @app.get("/images/json")
    def images_list():
        return JSONResponse(store.list_images())

    @app.post("/images/create")
    def images_create():
        return PlainTextResponse(json.dumps({"status": "Download complete"}) + "\n", media_type="application/json")

    # ---- volumes ----
    @app.get("/volumes")
    def volumes_list():
        return JSONResponse(store.list_volumes())

    @app.post("/volumes/create")
    async def volume_create(request: Request):
        return JSONResponse(store.create_volume(await body(request)), status_code=201)

    @app.get("/volumes/{name}")
    def volume_inspect(name: str):
        try:
            return JSONResponse(store.inspect_volume(name))
        except NotFound:
            return nf(name)

    @app.delete("/volumes/{name}")
    def volume_remove(name: str):
        try:
            store.remove_volume(name)
            return Response(status_code=204)
        except NotFound:
            return nf(name)

    # ---- networks ----
    @app.get("/networks")
    def networks_list():
        return JSONResponse(store.list_networks())

    @app.post("/networks/create")
    async def network_create(request: Request):
        return JSONResponse(store.create_network(await body(request)), status_code=201)

    @app.get("/networks/{ref}")
    def network_inspect(ref: str):
        try:
            return JSONResponse(store.inspect_network(ref))
        except NotFound:
            return nf(ref)

    @app.delete("/networks/{ref}")
    def network_remove(ref: str):
        try:
            store.remove_network(ref)
            return Response(status_code=204)
        except NotFound:
            return nf(ref)

    # ---- containers ----
    @app.get("/containers/json")
    def containers_list(all: bool = False):  # noqa: A002 - Docker's query param is `all`
        return JSONResponse(store.list_containers(all))

    @app.post("/containers/create")
    async def container_create(request: Request, name: str | None = None):
        return JSONResponse(store.create_container(name, await body(request)), status_code=201)

    @app.get("/containers/{ref}/json")
    def container_inspect(ref: str):
        try:
            return JSONResponse(store.inspect_container(ref))
        except NotFound:
            return nf(ref)

    @app.post("/containers/{ref}/start")
    def container_start(ref: str):
        try:
            store.start_container(ref)
            return Response(status_code=204)
        except NotFound:
            return nf(ref)

    @app.post("/containers/{ref}/stop")
    def container_stop(ref: str):
        try:
            store.stop_container(ref)
            return Response(status_code=204)
        except NotFound:
            return nf(ref)

    @app.delete("/containers/{ref}")
    def container_remove(ref: str, force: bool = False):
        try:
            store.remove_container(ref)
            return Response(status_code=204)
        except NotFound:
            return nf(ref)

    @app.post("/containers/{ref}/exec")
    async def exec_create(ref: str, request: Request):
        try:
            return JSONResponse(store.create_exec(ref, await body(request)), status_code=201)
        except NotFound:
            return nf(ref)

    @app.get("/exec/{eid}/json")
    def exec_inspect(eid: str):
        try:
            return JSONResponse(store.inspect_exec(eid))
        except NotFound:
            return nf(eid)

    # ---- containers: logs / wait ----
    @app.get("/containers/{ref}/logs")
    def container_logs(ref: str):
        try:
            store.inspect_container(ref)  # existence check
        except NotFound:
            return nf(ref)
        return PlainTextResponse("sim: log line 1\nsim: log line 2\n")

    @app.post("/containers/{ref}/wait")
    def container_wait(ref: str):
        try:
            return JSONResponse(store.wait_container(ref))
        except NotFound:
            return nf(ref)

    # ---- build (B5/B6 drift) ----
    @app.post("/build")
    def build():
        # Both engines expose /build; Podman does a classic buildah build (no
        # BuildKit). Return a classic NDJSON build stream.
        return PlainTextResponse(
            json.dumps({"stream": "Step 1/1 : FROM alpine\n"}) + "\n" + json.dumps({"stream": "Successfully built deadbeef\n"}) + "\n",
            media_type="application/json",
        )

    @app.api_route("/session", methods=["POST", "GET"])
    @app.api_route("/grpc", methods=["POST", "GET"])
    def buildkit_session():
        # BuildKit session/gRPC endpoints exist only on Docker's embedded
        # BuildKit. Podman (buildah) does NOT serve them -> 404 (drift B5/B6).
        if not store.buildkit():
            return JSONResponse({"message": "BuildKit is not available on this engine"}, status_code=404)
        return JSONResponse({"note": "buildkit session not fully modeled by the simulator"}, status_code=200)

    @app.get("/{full_path:path}")
    def catch_all(full_path: str):
        return JSONResponse({"message": f"engine-sim: unhandled /{full_path}"}, status_code=404)

    return app

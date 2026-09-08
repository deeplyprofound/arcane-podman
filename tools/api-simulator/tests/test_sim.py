"""Deterministic gates for the engine simulator — in-process (no socket/VM).

Run: uv run pytest    (from tools/api-simulator/)

Covers the stateful lifecycle AND the encoded Docker<->Podman drifts, so the
two profiles stay a runnable version of docs/podman/drift-audit.md.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine_sim import build_app
from engine_sim import drift as drift_mod


@pytest.fixture
def podman():
    with TestClient(build_app("podman", db_url="sqlite+pysqlite:///:memory:")) as c:
        yield c


@pytest.fixture
def docker():
    with TestClient(build_app("docker", db_url="sqlite+pysqlite:///:memory:")) as c:
        yield c


# ------------------------------------------------------------------ drift matrix
def test_drift_registry_is_coherent():
    assert drift_mod.REGISTRY, "drift registry must not be empty"
    for d in drift_mod.REGISTRY:
        assert d.docker and d.podman, f"{d.id} must document both engines"
        assert d.docker != d.podman, f"{d.id} docker/podman behavior must differ"
        assert d.citation, f"{d.id} must cite a source"


# ------------------------------------------------------------------ engine identity
def test_engine_identity_podman(podman):
    v = podman.get("/v1.44/version").json()
    assert v["Components"][0]["Name"] == "Podman Engine"
    ping = podman.get("/_ping")
    assert ping.headers.get("api-version") == "1.44"
    assert ping.headers.get("buildkit-version", "") == ""  # B5/B6: no BuildKit
    info = podman.get("/info").json()
    assert info["CgroupVersion"] == "2"
    assert any("rootless" in o for o in info["SecurityOptions"])  # B1
    assert any("selinux" in o for o in info["SecurityOptions"])   # B3


def test_engine_identity_docker(docker):
    v = docker.get("/version").json()
    assert "Docker" in v["Platform"]["Name"]
    assert docker.get("/_ping").headers.get("buildkit-version")  # docker has BuildKit
    info = docker.get("/info").json()
    assert not any("rootless" in o for o in info["SecurityOptions"])


# ------------------------------------------------------------------ B2 cgroup drift
def test_memory_swappiness_dropped_on_podman(podman):
    r = podman.post("/containers/create?name=c1", json={"Image": "alpine:latest", "HostConfig": {"MemorySwappiness": 60}})
    assert r.status_code == 201
    hc = podman.get("/containers/c1/json").json()["HostConfig"]
    assert hc["MemorySwappiness"] is None  # podman cgroup v2 drops it


def test_memory_swappiness_kept_on_docker(docker):
    docker.post("/containers/create?name=c1", json={"Image": "alpine:latest", "HostConfig": {"MemorySwappiness": 60}})
    hc = docker.get("/containers/c1/json").json()["HostConfig"]
    assert hc["MemorySwappiness"] == 60


# ------------------------------------------------------------------ B4/B5/B6 gating surface
def test_swarm_absent_on_podman(podman):
    assert podman.get("/swarm").status_code == 404


def test_swarm_present_but_inactive_on_docker(docker):
    assert docker.get("/swarm").status_code == 503  # exists, "not a manager"


def test_buildkit_session_absent_on_podman(podman):
    assert podman.post("/session").status_code == 404
    assert podman.post("/grpc").status_code == 404


def test_buildkit_session_present_on_docker(docker):
    assert docker.post("/session").status_code == 200


# ------------------------------------------------------------------ D2 default network
def test_default_network_is_bridge_over_compat(podman):
    names = [n["Name"] for n in podman.get("/networks").json()]
    assert "bridge" in names  # compat aliases the default; NOT "podman"


# ------------------------------------------------------------------ full container lifecycle
def test_container_lifecycle(podman):
    podman.post("/containers/create?name=life", json={"Image": "alpine:latest", "Cmd": ["sleep", "1"]})
    assert podman.get("/containers/life/json").json()["State"]["Status"] == "created"
    podman.post("/containers/life/start")
    assert podman.get("/containers/life/json").json()["State"]["Running"] is True
    podman.post("/containers/life/pause")
    st = podman.get("/containers/life/json").json()["State"]
    assert st["Status"] == "paused" and st["Paused"] is True
    podman.post("/containers/life/unpause")
    assert podman.get("/containers/life/json").json()["State"]["Status"] == "running"
    assert len(podman.get("/containers/json").json()) == 1  # running list
    podman.post("/containers/life/restart")
    assert podman.get("/containers/life/json").json()["State"]["Running"] is True
    podman.post("/containers/life/kill")
    ex = podman.get("/containers/life/json").json()["State"]
    assert ex["Status"] == "exited" and ex["ExitCode"] == 137
    assert podman.post("/containers/life/wait").json()["StatusCode"] == 137
    podman.delete("/containers/life?force=true")
    assert podman.get("/containers/life/json").status_code == 404


def test_container_pause_conflict_when_not_running(podman):
    podman.post("/containers/create?name=cx", json={"Image": "alpine:latest"})
    assert podman.post("/containers/cx/pause").status_code == 409  # created, not running


def test_container_prune_removes_exited(podman):
    podman.post("/containers/create?name=p1", json={"Image": "alpine:latest"})
    podman.post("/containers/p1/start")
    podman.post("/containers/p1/stop")
    out = podman.post("/containers/prune").json()
    assert len(out["ContainersDeleted"]) == 1


def test_container_stats_snapshot(podman):
    podman.post("/containers/create?name=s1", json={"Image": "alpine:latest"})
    stats = podman.get("/containers/s1/stats?stream=false").json()
    assert stats["memory_stats"]["limit"] > 0


# ------------------------------------------------------------------ images
def test_image_pull_qualifies_and_preserves_registry(podman):
    podman.post("/images/create?fromImage=busybox&tag=latest")
    podman.post("/images/create?fromImage=myreg.io/team/app&tag=v1")
    tags = [t for i in podman.get("/images/json").json() for t in (i["RepoTags"] or [])]
    assert "docker.io/library/busybox:latest" in tags
    assert "myreg.io/team/app:v1" in tags


def test_image_tag_inspect_history_remove(podman):
    podman.post("/images/create?fromImage=busybox&tag=latest")
    assert podman.post("/images/docker.io/library/busybox:latest/tag?repo=mybox&tag=v2").status_code == 201
    insp = podman.get("/images/docker.io/library/busybox:latest/json").json()
    assert "docker.io/library/mybox:v2" in insp["RepoTags"]
    assert podman.get("/images/docker.io/library/busybox:latest/history").json()[0]["Size"] >= 0
    assert podman.delete("/images/docker.io/library/busybox:latest").status_code == 200


# ------------------------------------------------------------------ volumes / networks
def test_volume_crud(podman):
    assert podman.post("/volumes/create", json={"Name": "v1"}).json()["Name"] == "v1"
    assert podman.get("/volumes/v1").json()["Driver"] == "local"
    assert any(v["Name"] == "v1" for v in podman.get("/volumes").json()["Volumes"])
    assert podman.delete("/volumes/v1").status_code == 204
    assert podman.get("/volumes/v1").status_code == 404


def test_network_crud_connect(podman):
    nid = podman.post("/networks/create", json={"Name": "n1", "Driver": "bridge"}).json()["Id"]
    assert podman.get(f"/networks/{nid}").json()["Name"] == "n1"
    assert podman.post(f"/networks/{nid}/connect", json={"Container": "c1"}).status_code == 200
    assert podman.post(f"/networks/{nid}/disconnect", json={"Container": "c1"}).status_code == 200
    assert podman.delete(f"/networks/{nid}").status_code == 204


# ------------------------------------------------------------------ df shape (D7)
def test_df_has_docker_shape(podman):
    df = podman.get("/v1.44/system/df").json()
    for k in ("LayersSize", "Images", "Containers", "Volumes", "BuildCache"):
        assert k in df, f"df must have Docker-shape key {k}"


# ------------------------------------------------------------------ events
def test_events_recorded(podman):
    podman.post("/volumes/create", json={"Name": "ev1"})
    lines = [ln for ln in podman.get("/events").text.splitlines() if ln.strip()]
    actions = [__import__("json").loads(ln)["Action"] for ln in lines]
    assert "create" in actions


# ------------------------------------------------------------------ D4 rootless ports
def _publish(port):
    return {"Image": "alpine:latest", "HostConfig": {"PortBindings": {f"{port}/tcp": [{"HostPort": str(port)}]}}}


def test_rootless_cannot_publish_privileged_port(podman):
    podman.post("/containers/create?name=p80", json=_publish(80))
    r = podman.post("/containers/p80/start")
    assert r.status_code == 500 and "privileged port" in r.json()["message"]  # D4


def test_rootless_can_publish_high_port(podman):
    podman.post("/containers/create?name=p8080", json=_publish(8080))
    assert podman.post("/containers/p8080/start").status_code == 204


def test_docker_can_publish_privileged_port(docker):
    docker.post("/containers/create?name=d80", json=_publish(80))
    assert docker.post("/containers/d80/start").status_code == 204  # rootful


# ------------------------------------------------------------------ D5 restart policy (moot)
def test_restart_policy_name_preserved(podman):
    for pol in ("unless-stopped", "always", "on-failure", "no"):
        podman.post(f"/containers/create?name=rp_{pol.replace('-', '_')}",
                    json={"Image": "alpine:latest", "HostConfig": {"RestartPolicy": {"Name": pol}}})
        got = podman.get(f"/containers/rp_{pol.replace('-', '_')}/json").json()["HostConfig"]["RestartPolicy"]["Name"]
        assert got == pol  # compat preserves the name verbatim (D5 moot)

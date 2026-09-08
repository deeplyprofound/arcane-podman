from __future__ import annotations

import argparse
import os

import uvicorn

from . import drift as drift_mod
from .api import build_app


def _print_drift_report() -> None:
    print("Docker ↔ Podman drifts encoded by the simulator "
          f"({len(drift_mod.REGISTRY)}):\n")
    for d in drift_mod.REGISTRY:
        print(f"[{d.id}]  ({d.area})  {d.summary}")
        print(f"    docker : {d.docker}")
        print(f"    podman : {d.podman}")
        print(f"    source : {d.citation}\n")


def main() -> None:
    ap = argparse.ArgumentParser(prog="engine-sim", description="Stateful Docker/Podman engine API simulator")
    ap.add_argument("--profile", choices=["podman", "docker"], default=os.environ.get("SIM_PROFILE", "podman"))
    ap.add_argument("--socket", default=os.environ.get("SIM_SOCKET", "/tmp/arcane-sim.sock"), help="unix socket to listen on")
    ap.add_argument("--port", type=int, default=0, help="listen on TCP port instead of a unix socket when > 0")
    ap.add_argument("--db", default=os.environ.get("SIM_DB", "sqlite+pysqlite:///:memory:"), help="SQLAlchemy state DB url")
    ap.add_argument("--drift-report", action="store_true", help="print the encoded Docker<->Podman drift matrix and exit")
    args = ap.parse_args()

    if args.drift_report:
        _print_drift_report()
        return

    app = build_app(args.profile, db_url=args.db)
    if args.port > 0:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    else:
        if os.path.exists(args.socket):
            os.unlink(args.socket)
        uvicorn.run(app, uds=args.socket, log_level="warning")


if __name__ == "__main__":
    main()

"""Load the real captured API responses that ground the profiles."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# Works both from source (tools/api-simulator/fixtures) and from an installed
# wheel (engine_sim/fixtures via force-include).
_CANDIDATES = [_HERE / "fixtures", _HERE.parent / "fixtures"]


def fixtures_dir(profile: str) -> Path:
    for base in _CANDIDATES:
        if (base / profile).is_dir():
            return base / profile
    return _CANDIDATES[-1] / profile


@lru_cache(maxsize=None)
def load_json(profile: str, name: str) -> dict | list | None:
    path = fixtures_dir(profile) / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def load_ping_headers(profile: str) -> dict[str, str]:
    path = fixtures_dir(profile) / "_ping.headers.txt"
    headers: dict[str, str] = {}
    if not path.exists():
        return headers
    for line in path.read_text().splitlines():
        if ":" in line and not line.upper().startswith("HTTP/"):
            k, _, v = line.partition(":")
            headers[k.strip()] = v.strip()
    return headers

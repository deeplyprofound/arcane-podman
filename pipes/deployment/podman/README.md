# Podman deployment (quadlets) — WIP scaffold

Podman doesn't use Docker Compose the way `../docker/` does; it runs containers
as **systemd services generated from [quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
unit files** (`.container`, `.pod`, `.network`, `.volume`). This folder holds
those units for running Arcane-Podman under systemd (rootful or rootless).

> **Status: scaffold.** These units are a starting point. They will be
> finalized and **tested on a local server** once the podman-support rework
> settles. Do not assume they're production-ready yet.

## Layout (planned)

- `arcane.container` — the Arcane manager as a quadlet unit (starter provided)
- `arcane.volume` — named data volume (add when finalized)
- `arcane.network` — dedicated network (add if needed)
- `arcane.pod` — pod grouping manager + agent (add if needed)

## Install (rootless, once finalized)

```sh
mkdir -p ~/.config/containers/systemd
cp arcane.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start arcane.service
```

For rootful, drop the units in `/etc/containers/systemd/` and use `systemctl`
(no `--user`).

## Key detail: Arcane talks to Podman's socket

Arcane auto-detects the Podman socket when `DOCKER_HOST` is unset (see the
socket auto-detection in `server/backend`). Under rootless Podman that socket is
`%t/podman/podman.sock` (i.e. `$XDG_RUNTIME_DIR/podman/podman.sock`); enable it
with `systemctl --user enable --now podman.socket`.

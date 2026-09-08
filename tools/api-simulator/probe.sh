#!/usr/bin/env bash
# Probe a live container engine's Docker-compatible API end-to-end and capture
# every response as a fixture. Grounds the simulator's ORM models + profile
# serializers in REAL data instead of guesses.
#
#   DOCKER_HOST=unix://… ./probe.sh podman   # captures into fixtures/podman/
#
# Exercises a full lifecycle so state-transition shapes (Created→Running→Exited)
# are captured, not just empty lists.
set -uo pipefail

PROFILE="${1:-podman}"
SOCK="${DOCKER_HOST#unix://}"
DIR="$(cd "$(dirname "$0")" && pwd)/fixtures/${PROFILE}"
mkdir -p "$DIR"

c() { curl -s --unix-socket "$SOCK" "$@"; }
jget() { c "http://d$1" | python3 -m json.tool 2>/dev/null; }
save() { # save <name> <method> <path> [body]
  local name="$1" method="$2" path="$3" body="${4:-}"
  if [ -n "$body" ]; then
    c -X "$method" -H 'Content-Type: application/json' -d "$body" "http://d${path}" | python3 -m json.tool 2>/dev/null > "$DIR/$name.json" || true
  else
    c -X "$method" "http://d${path}" | python3 -m json.tool 2>/dev/null > "$DIR/$name.json" || true
  fi
  printf '  %-28s %s %s\n' "$name.json" "$method" "$path"
}

echo "== probing $PROFILE via $SOCK -> $DIR =="

# --- system ---
c -i "http://d/_ping" | sed 's/\r$//' > "$DIR/_ping.headers.txt"; echo "  _ping.headers.txt"
save version   GET /version
save info      GET /info
save df        GET "/system/df"

# --- images ---
c -X POST "http://d/images/create?fromImage=docker.io/library/alpine&tag=latest" -o /dev/null
save images_list   GET /images/json
save image_inspect GET /images/alpine:latest/json
save image_history GET /images/alpine:latest/history

# --- volumes ---
c -X POST -H 'Content-Type: application/json' -d '{"Name":"sim_probe_vol"}' "http://d/volumes/create" | python3 -m json.tool > "$DIR/volume_create.json" 2>/dev/null
printf '  %-28s POST /volumes/create\n' volume_create.json
save volume_inspect GET /volumes/sim_probe_vol
save volumes_list   GET /volumes

# --- networks ---
c -X POST -H 'Content-Type: application/json' -d '{"Name":"sim_probe_net","Driver":"bridge"}' "http://d/networks/create" | python3 -m json.tool > "$DIR/network_create.json" 2>/dev/null
printf '  %-28s POST /networks/create\n' network_create.json
save networks_list  GET /networks
NETID=$(python3 -c "import json;print(json.load(open('$DIR/network_create.json')).get('Id',''))" 2>/dev/null)
[ -n "$NETID" ] && save network_inspect GET "/networks/$NETID"

# --- container lifecycle ---
CREATE_BODY='{"Image":"alpine:latest","Cmd":["sleep","30"],"HostConfig":{"Memory":67108864,"MemorySwappiness":60,"Binds":["sim_probe_vol:/data"]}}'
c -X POST -H 'Content-Type: application/json' -d "$CREATE_BODY" "http://d/containers/create?name=sim_probe_ctr" | python3 -m json.tool > "$DIR/container_create.json" 2>/dev/null
printf '  %-28s POST /containers/create\n' container_create.json
CID=$(python3 -c "import json;print(json.load(open('$DIR/container_create.json')).get('Id',''))" 2>/dev/null)
echo "  container id: ${CID:0:12}"
if [ -n "$CID" ]; then
  save container_inspect_created GET "/containers/$CID/json"
  c -X POST "http://d/containers/$CID/start" -o /dev/null
  save container_inspect_running GET "/containers/$CID/json"
  save containers_list           GET "/containers/json?all=true"
  # exec
  c -X POST -H 'Content-Type: application/json' -d '{"AttachStdout":true,"AttachStderr":true,"Cmd":["echo","hi"]}' "http://d/containers/$CID/exec" | python3 -m json.tool > "$DIR/exec_create.json" 2>/dev/null
  printf '  %-28s POST /containers/{id}/exec\n' exec_create.json
  EXECID=$(python3 -c "import json;print(json.load(open('$DIR/exec_create.json')).get('Id',''))" 2>/dev/null)
  [ -n "$EXECID" ] && { c -X POST -H 'Content-Type: application/json' -d '{"Detach":false}' "http://d/exec/$EXECID/start" -o /dev/null; save exec_inspect GET "/exec/$EXECID/json"; }
  c -X POST "http://d/containers/$CID/stop?t=1" -o /dev/null
  save container_inspect_exited GET "/containers/$CID/json"
  # cleanup
  c -X DELETE "http://d/containers/$CID?force=true" -o /dev/null
fi

# cleanup probe artifacts
c -X DELETE "http://d/volumes/sim_probe_vol?force=true" -o /dev/null
[ -n "${NETID:-}" ] && c -X DELETE "http://d/networks/$NETID" -o /dev/null

echo "== done: $(ls "$DIR" | wc -l | tr -d ' ') fixtures in $DIR =="

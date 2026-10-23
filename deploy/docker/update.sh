#!/usr/bin/env bash
# Update (or roll back) the single goldmic container without cutting off running work.
#
#   sudo bash /opt/goldmic/build/deploy/docker/update.sh              # build build/ and switch to it
#   sudo bash /opt/goldmic/build/deploy/docker/update.sh --rollback   # switch back to goldmic:previous
#   add --force when the running container is broken (crash loop, hung, no answer on the drain endpoint)
#
# 1. The new image is built as goldmic:next while the old container keeps serving.
# 2. The old container is drained (no new tasks, uploads, matching or AI writing) until both counts are 0:
#    active_tasks (production, exports, task operations, deletions) and upload_jobs (uploads whose paid
#    speech recognition is still running). A drain that does not finish within GM_DRAIN_TIMEOUT seconds
#    (default 7200) is cancelled and nothing changes; running work is not killed for an update.
# 3. goldmic:previous keeps the image that was running; goldmic:latest becomes the new one; compose recreates.
# 4. The new container must be healthy and /health/ready within GM_HEALTH_TIMEOUT seconds (default 300),
#    otherwise the previous image is put back. Data, samples and goldmic.env are never touched.
# 5. One run at a time (flock on $GM_ROOT/.update.lock). If the script stops after the drain started
#    (Ctrl-C, closed SSH session, failed step) it cancels the drain, so run it in tmux or screen.
# --force skips the drain and the waiting. Without it, a container that does not answer the drain request makes
#    the script stop and change nothing. With it, work still running in that container is lost, and compose
#    waits at most 30 s for the container to stop instead of the 2 h stop grace period.
set -euo pipefail

ROOT=${GM_ROOT:-/opt/goldmic}
CONTAINER=goldmic
DRAIN_TIMEOUT=${GM_DRAIN_TIMEOUT:-7200}
HEALTH_TIMEOUT=${GM_HEALTH_TIMEOUT:-300}
MODE=update
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --rollback) MODE=rollback ;;
    --force) FORCE=1 ;;
    -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) echo "usage: $0 [--rollback] [--force]" >&2; exit 2 ;;
  esac
done
[[ "$DRAIN_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || { echo "GM_DRAIN_TIMEOUT must be a positive integer" >&2; exit 2; }
[[ "$HEALTH_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || { echo "GM_HEALTH_TIMEOUT must be a positive integer" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is not installed" >&2; exit 2; }
docker compose version >/dev/null
test -f "$ROOT/docker-compose.yml" || { echo "$ROOT/docker-compose.yml is missing" >&2; exit 2; }
command -v flock >/dev/null || { echo "flock (util-linux) is not installed" >&2; exit 2; }
# Two runs would race on goldmic:next and could overwrite the rollback image.
exec 9>"$ROOT/.update.lock"
flock -n 9 || { echo "another update.sh is already running (lock: $ROOT/.update.lock)" >&2; exit 2; }
if ! grep -Eq '^[[:space:]]*stop_grace_period:[[:space:]]*7200s' "$ROOT/docker-compose.yml"; then
  echo "warning: $ROOT/docker-compose.yml should set stop_grace_period: 7200s (see build/deploy/docker/docker-compose.yml)" >&2
fi

compose() { docker compose -f "$ROOT/docker-compose.yml" "$@"; }
image_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null || true; }
running() { [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)" = true ]; }
running_image() { docker inspect -f '{{.Image}}' "$CONTAINER" 2>/dev/null || true; }
# The image the service really runs, not whatever goldmic:latest points at (a manual docker build -t goldmic:latest
# or an interrupted run can make them differ): this is what goldmic:previous and the automatic restore must use.
current_image() { if running; then running_image; else image_id goldmic:latest; fi; }

# The admin endpoints answer loopback clients only, so ask from inside the container.
# Prints the running work: active_tasks plus upload_jobs (absent in a container that predates it: 0).
admin() {  # admin GET|POST|DELETE
  docker exec "$CONTAINER" python -c '
import json, sys, urllib.request
request = urllib.request.Request("http://127.0.0.1:8000/api/admin/drain", method=sys.argv[1], headers={"Host": "localhost"})
with urllib.request.urlopen(request, timeout=15) as response:
    data = json.load(response)
print(int(data["active_tasks"]) + int(data.get("upload_jobs", 0)))' "$1"
}

# One failed call (a 15 s timeout, a docker hiccup) must not throw away up to two hours of draining,
# nor decide that the drain could not start.
admin_try() {  # admin_try GET|POST
  local failures=0 value
  until value=$(admin "$1"); do
    failures=$((failures + 1))
    [ "$failures" -lt 5 ] || return 1
    sleep 5
  done
  echo "$value"
}

wait_ready() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT)) state
  while [ "$SECONDS" -lt "$deadline" ]; do
    state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER" 2>/dev/null || true)
    if [ "$state" = healthy ] && docker exec "$CONTAINER" curl -fsS -o /dev/null -H "Host: localhost" http://127.0.0.1:8000/health/ready; then
      return 0
    fi
    sleep 5
  done
  return 1
}

PHASE=start
DRAIN_STARTED=0
STOP_TIMEOUT=
OLD=
PREVIOUS=
put_tags_back() {  # goldmic:latest and goldmic:previous as they were before this run
  [ -z "$OLD" ] || docker tag "$OLD" goldmic:latest
  if [ -n "$PREVIOUS" ]; then
    docker tag "$PREVIOUS" goldmic:previous
  else
    docker rmi goldmic:previous >/dev/null 2>&1 || true
  fi
}
cancel_drain() {
  if admin DELETE >/dev/null 2>&1; then
    echo "Drain cancelled: the old version keeps serving and accepts new work again." >&2
  else
    echo "The drain could not be cancelled: run" >&2
    echo "  docker exec $CONTAINER curl -fsS -X DELETE -H 'Host: localhost' http://127.0.0.1:8000/api/admin/drain" >&2
  fi
}
# Registered by the EXIT trap below; ShellCheck cannot follow this callback (SC2317 in 0.9, SC2329 in 0.11).
# shellcheck disable=SC2317,SC2329
on_exit() {
  local status=$?
  set +e
  trap '' INT TERM HUP  # a second Ctrl-C must not abort the cleanup below
  # The old container refuses new work while it drains. If the script stops early, or compose fails
  # before it replaced that container, the drain must not outlive us (a recreated container has none).
  if [ "$status" -ne 0 ] && running; then
    case "$PHASE" in
      draining) [ "$DRAIN_STARTED" != 1 ] || cancel_drain ;;
      switching)  # stopped before compose replaced the container: undo the tag moves too
        if [ -n "$OLD" ] && [ "$(running_image)" = "$OLD" ]; then
          put_tags_back
          [ "$DRAIN_STARTED" != 1 ] || cancel_drain
        fi ;;
    esac
  fi
  exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT TERM
trap 'exit 129' HUP

if [ "$MODE" = update ]; then
  test -f "$ROOT/build/deploy/docker/Dockerfile" || { echo "$ROOT/build is not a complete build context" >&2; exit 2; }
  PHASE=build
  echo "Building goldmic:next from $ROOT/build (the running version keeps serving) ..."
  docker build -f "$ROOT/build/deploy/docker/Dockerfile" -t goldmic:next "$ROOT/build"
  TARGET=goldmic:next
else
  [ -n "$(image_id goldmic:previous)" ] || { echo "there is no goldmic:previous image to roll back to" >&2; exit 1; }
  TARGET=goldmic:previous
fi
if [ "$(image_id "$TARGET")" = "$(current_image)" ] && running; then
  echo "$TARGET is already the running image; nothing to do."
  PHASE=finished
  exit 0
fi

if running && [ "$FORCE" = 1 ]; then
  echo "--force: not draining. Work still running in $CONTAINER is lost." >&2
  STOP_TIMEOUT=30
elif running; then
  PHASE=draining
  echo "Draining: no new work is accepted; waiting for running work to finish (up to ${DRAIN_TIMEOUT}s) ..."
  if ! active=$(admin_try POST); then
    admin DELETE >/dev/null 2>&1 || true  # a request whose answer was lost may still have been applied
    echo "The running container does not answer the drain request (crash loop, hung, not ready?). Nothing was changed." >&2
    echo "  look at:  docker logs $CONTAINER" >&2
    echo "  if it is broken and nothing worth keeping runs in it, run this script again with --force" >&2
    exit 1
  fi
  DRAIN_STARTED=1
  deadline=$((SECONDS + DRAIN_TIMEOUT))
  last_report=$SECONDS
  while [ "$active" != 0 ]; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "Drain timed out with $active unit(s) of work still running; not switching." >&2
      exit 1
    fi
    if [ $((SECONDS - last_report)) -ge 60 ]; then
      echo "  still $active unit(s) of work running (tasks, exports, or uploads being transcribed) ..."
      last_report=$SECONDS
    fi
    sleep 5
    if ! active=$(admin_try GET); then
      echo "Lost contact with $CONTAINER while draining (restarted? crashed?); not switching." >&2
      exit 1
    fi
  done
fi

OLD=$(current_image)
PREVIOUS=$(image_id goldmic:previous)
PHASE=switching
if [ "$MODE" = update ]; then
  [ -z "$OLD" ] || docker tag "$OLD" goldmic:previous
  docker tag goldmic:next goldmic:latest
else
  [ -z "$OLD" ] || docker tag "$OLD" goldmic:rolled-back
  docker tag goldmic:previous goldmic:latest
fi
echo "Switching to $(image_id goldmic:latest) ..."
if compose up -d ${STOP_TIMEOUT:+-t "$STOP_TIMEOUT"}; then
  if wait_ready; then
    PHASE=finished
    echo "Done: $CONTAINER is healthy and ready on $(image_id goldmic:latest)."
    exit 0
  fi
  echo "The new container did not become healthy and ready within ${HEALTH_TIMEOUT}s." >&2
else
  echo "docker compose could not start the new container." >&2
fi
if [ -z "$OLD" ]; then
  echo "There is no earlier image to go back to; inspect it with: docker logs $CONTAINER" >&2
  PHASE=failed
  exit 1
fi
put_tags_back  # a failed attempt leaves the tags as they were
if running && [ "$(running_image)" = "$OLD" ]; then
  # compose failed before it replaced the container: the earlier version never stopped serving.
  [ "$DRAIN_STARTED" != 1 ] || cancel_drain
  echo "The earlier version is still the running container; nothing was switched." >&2
  PHASE=failed
  exit 1
fi
echo "Putting the earlier image back ..." >&2
if compose up -d -t 60 && wait_ready; then
  echo "The earlier version is serving again. Inspect the failed image before retrying." >&2
else
  echo "The earlier version did not become ready either: check docker logs $CONTAINER now." >&2
fi
PHASE=failed
exit 1

#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 || $# -ne 1 || ! $1 =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Usage: sudo $0 <existing-release-id>" >&2
  exit 1
fi
for command_name in curl python3 systemctl readlink seq ln; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "Missing required command: ${command_name}" >&2
    exit 1
  }
done
TARGET=/opt/golden-mic/releases/$1
for required_path in \
  .venv/bin/python \
  backend/main.py \
  deploy/verify_python_environment.py \
  frontend/dist/index.html \
  frontend/dist/ASSET_MANIFEST.sha256 \
  pyproject.toml \
  uv.lock \
  vendor/scenedetect-0.7.1-py3-none-any.whl; do
  if [[ ! -e ${TARGET}/${required_path} ]]; then
    echo "Rollback target is missing ${required_path}: ${TARGET}" >&2
    exit 1
  fi
done
if [[ ! -x ${TARGET}/.venv/bin/python ]]; then
  echo "Rollback target Python is not executable: ${TARGET}" >&2
  exit 1
fi
"${TARGET}/.venv/bin/python" "${TARGET}/deploy/verify_python_environment.py"

CURRENT=$(readlink -f /opt/golden-mic/current || true)
if [[ -z ${CURRENT} || ! -d ${CURRENT} ]]; then
  echo "Current release symlink is invalid; use deploy_release.sh for recovery." >&2
  exit 1
fi
if [[ ${CURRENT} == "${TARGET}" ]]; then
  echo "Release $1 is already current."
  exit 0
fi

DRAINED_CURRENT_SERVICE=0
SYMLINK_SWITCHED=0
ROLLBACK_COMMITTED=0
# Registered by the EXIT trap below; ShellCheck 0.9 cannot follow this callback.
# shellcheck disable=SC2317
restore_current_on_exit() {
  exit_code=$?
  trap - EXIT INT TERM
  if (( exit_code != 0 && ROLLBACK_COMMITTED == 0 )); then
    if (( SYMLINK_SWITCHED == 1 )); then
      ln -sfn "${CURRENT}" /opt/golden-mic/current
      systemctl restart golden-mic.service || true
    elif (( DRAINED_CURRENT_SERVICE == 1 )) && systemctl is-active --quiet golden-mic.service; then
      curl --fail --silent --request DELETE \
        http://127.0.0.1:8000/api/admin/drain >/dev/null || true
    fi
  fi
  exit "${exit_code}"
}
trap restore_current_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if systemctl is-active --quiet golden-mic.service; then
  if curl --fail --silent http://127.0.0.1:8000/health/live >/dev/null; then
    curl --fail --silent --request POST http://127.0.0.1:8000/api/admin/drain >/dev/null
    DRAINED_CURRENT_SERVICE=1
    active=1
    for _ in $(seq 1 1440); do
      active=$(curl --fail --silent http://127.0.0.1:8000/api/admin/drain | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["active_tasks"]))')
      if (( active == 0 )); then
        break
      fi
      sleep 5
    done
    if (( active != 0 )); then
      echo "Rollback drain timed out; no release was switched." >&2
      exit 1
    fi
  else
    echo "Current service is active but unhealthy; proceeding with emergency rollback." >&2
  fi
fi

ln -sfn "${TARGET}" /opt/golden-mic/current
SYMLINK_SWITCHED=1
systemctl restart golden-mic.service
for _ in $(seq 1 60); do
  if curl --fail --silent http://127.0.0.1:8000/health/ready >/dev/null; then
    systemctl reload nginx
    ROLLBACK_COMMITTED=1
    trap - EXIT INT TERM
    echo "Rolled back to $1"
    exit 0
  fi
  sleep 2
done

echo "Rollback target failed readiness; restoring ${CURRENT}." >&2
exit 1

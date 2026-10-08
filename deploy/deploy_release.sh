#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  sudo ./deploy/deploy_release.sh --archive /path/golden-mic-<version>.tar.gz \
    --checksum /path/golden-mic-<version>.tar.gz.sha256 \
    --signature /path/golden-mic-<version>.tar.gz.minisig \
    [--release-id 20260728-120000] [--drain-timeout 7200]

The archive must be produced by deploy/build_release.py. Deployment drains the
current single-worker service, switches the current symlink, verifies readiness,
and automatically rolls back if startup fails.
EOF
}

ARCHIVE=""
CHECKSUM=""
SIGNATURE=""
RELEASE_ID=$(date -u +%Y%m%d-%H%M%S)
DRAIN_TIMEOUT=7200
while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive) ARCHIVE=${2:-}; shift 2 ;;
    --checksum) CHECKSUM=${2:-}; shift 2 ;;
    --signature) SIGNATURE=${2:-}; shift 2 ;;
    --release-id) RELEASE_ID=${2:-}; shift 2 ;;
    --drain-timeout) DRAIN_TIMEOUT=${2:-}; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ ${EUID} -ne 0 ]]; then
  echo "Deployment must run as root." >&2
  exit 1
fi
if [[ -z ${ARCHIVE} || ! -f ${ARCHIVE} ]]; then
  echo "Release archive is missing." >&2
  exit 1
fi
if [[ -z ${CHECKSUM} || ! -f ${CHECKSUM} ]]; then
  echo "Release checksum file is required." >&2
  exit 1
fi
if [[ -z ${SIGNATURE} || ! -f ${SIGNATURE} ]]; then
  echo "Minisign signature file is required." >&2
  exit 1
fi
PUBLIC_KEY=/etc/golden-mic/release.pub
if [[ ! -f ${PUBLIC_KEY} ]]; then
  echo "Release verification public key is missing: ${PUBLIC_KEY}" >&2
  exit 1
fi
if [[ ! ${RELEASE_ID} =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Invalid release id." >&2
  exit 1
fi
if [[ ! ${DRAIN_TIMEOUT} =~ ^[0-9]+$ ]] || (( DRAIN_TIMEOUT < 1 )); then
  echo "Drain timeout must be a positive integer." >&2
  exit 1
fi
for command_name in uv python3 tar curl sha256sum minisign systemctl readlink grep install awk stat date seq ln rm chmod chown journalctl mountpoint runuser mktemp; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "Missing required command: ${command_name}" >&2
    exit 1
  }
done
if ! mountpoint --quiet /srv/golden-mic-data; then
  echo "/srv/golden-mic-data is not a mounted CBS filesystem; deployment aborted." >&2
  exit 1
fi

STAGED_ARCHIVE=$(mktemp /opt/golden-mic/.release-XXXXXX.tar.gz)
TARGET=""
TARGET_CREATED=0
cleanup_staged_archive() {
  rm -f -- "${STAGED_ARCHIVE}"
}
cleanup_before_drain() {
  exit_code=$?
  trap - EXIT INT TERM
  cleanup_staged_archive
  if (( exit_code != 0 && TARGET_CREATED == 1 )) && [[ -n ${TARGET} && -d ${TARGET} ]]; then
    rm -rf -- "${TARGET}"
  fi
  exit "${exit_code}"
}
trap cleanup_before_drain EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
install -o root -g root -m 0600 "${ARCHIVE}" "${STAGED_ARCHIVE}"

expected_hash=$(awk 'NR == 1 { print $1 }' "${CHECKSUM}")
actual_hash=$(sha256sum "${STAGED_ARCHIVE}" | awk '{ print $1 }')
if [[ -z ${expected_hash} || ${actual_hash} != "${expected_hash}" ]]; then
  echo "Release checksum verification failed." >&2
  exit 1
fi
minisign -Vm "${STAGED_ARCHIVE}" -x "${SIGNATURE}" -p "${PUBLIC_KEY}"

ENV_FILE=/etc/golden-mic/golden-mic.env
if [[ ! -f ${ENV_FILE} ]]; then
  echo "Production environment file is missing: ${ENV_FILE}" >&2
  exit 1
fi
if grep -Eq 'CHANGE_ME|YOUR_DOMAIN|VIDEO_PROCESSING_PROVIDER|VOLCENGINE_MEDIAKIT_' "${ENV_FILE}"; then
  echo "Production environment contains placeholders or removed MediaKit settings." >&2
  exit 1
fi
if [[ $(stat -c '%a' "${ENV_FILE}") != "640" ]]; then
  echo "Production environment must have mode 0640." >&2
  exit 1
fi
if [[ $(stat -c '%U:%G' "${ENV_FILE}") != "root:goldenmic" ]]; then
  echo "Production environment must be owned by root:goldenmic." >&2
  exit 1
fi
if [[ $(stat -c '%U:%G' "${PUBLIC_KEY}") != "root:root" ]]; then
  echo "Release public key must be owned by root:root." >&2
  exit 1
fi
PUBLIC_KEY_MODE=$(stat -c '%a' "${PUBLIC_KEY}")
if [[ ${PUBLIC_KEY_MODE} != "644" && ${PUBLIC_KEY_MODE} != "444" ]]; then
  echo "Release public key must have mode 0644 or 0444." >&2
  exit 1
fi

RELEASE_ROOT=/opt/golden-mic/releases
TARGET=${RELEASE_ROOT}/${RELEASE_ID}
if [[ -e ${TARGET} ]]; then
  echo "Release already exists: ${TARGET}" >&2
  exit 1
fi
install -d -o root -g root -m 0755 "${TARGET}"
TARGET_CREATED=1
python3 "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/verify_release_archive.py" "${STAGED_ARCHIVE}"
tar --extract --gzip --file "${STAGED_ARCHIVE}" --strip-components=1 --directory "${TARGET}"
if [[ -e ${TARGET}/.env ]]; then
  echo "Release archive illegally contains .env." >&2
  rm -rf -- "${TARGET}"
  exit 1
fi
for required_path in \
  backend/main.py \
  deploy/verify_python_environment.py \
  deploy/validate_environment_file.py \
  deploy/verify_release_archive.py \
  frontend/dist/index.html \
  frontend/dist/ASSET_MANIFEST.sha256 \
  requirements-production.lock \
  uv.lock \
  pyproject.toml \
  vendor/scenedetect-0.7.1-py3-none-any.whl; do
  if [[ ! -f ${TARGET}/${required_path} ]]; then
    echo "Release is missing ${required_path}." >&2
    rm -rf -- "${TARGET}"
    exit 1
  fi
done

# The reviewed sample is never part of the archive. The host keeps it once at
# /opt/golden-mic/samples (registry.json + default/); every release gets a copy so a
# deploy never drops the demo. The server re-verifies every file's SHA-256 on use.
SAMPLE_SOURCE=/opt/golden-mic/samples
if [[ -f ${SAMPLE_SOURCE}/registry.json && ! -L ${SAMPLE_SOURCE} ]]; then
  rm -rf -- "${TARGET}/backend/assets/samples"
  cp -R --no-dereference -- "${SAMPLE_SOURCE}" "${TARGET}/backend/assets/samples"
fi

UV_BIN=$(command -v uv)
PYTHON_BIN=$(command -v python3)
install -d -o goldenmic -g goldenmic -m 0750 "${TARGET}/.venv"
runuser -u goldenmic -- env UV_CACHE_DIR=/srv/golden-mic-data/uv-cache \
  "${UV_BIN}" venv --python "${PYTHON_BIN}" --clear "${TARGET}/.venv"
runuser -u goldenmic -- env \
  UV_PROJECT_ENVIRONMENT="${TARGET}/.venv" \
  UV_CACHE_DIR=/srv/golden-mic-data/uv-cache \
  "${UV_BIN}" sync --frozen --no-dev --python "${PYTHON_BIN}" \
  --project "${TARGET}"

runuser -u goldenmic -- \
  "${TARGET}/.venv/bin/python" "${TARGET}/deploy/verify_python_environment.py"
runuser -u goldenmic -- \
  "${TARGET}/.venv/bin/python" "${TARGET}/deploy/validate_environment_file.py" \
  "${ENV_FILE}"
runuser -u goldenmic -- python3 "${TARGET}/deploy/run_with_environment.py" \
  --cwd /srv/golden-mic-data/tmp \
  --set PYTHONPATH="${TARGET}" \
  --set HOME=/nonexistent \
  --set TMPDIR=/srv/golden-mic-data/tmp \
  "${ENV_FILE}" \
  -- "${TARGET}/.venv/bin/python" -m backend.preflight
chown -R root:root "${TARGET}"
chmod -R go-w "${TARGET}"

OLD_TARGET=""
if [[ -L /opt/golden-mic/current ]]; then
  OLD_TARGET=$(readlink -f /opt/golden-mic/current)
fi

DRAINED_OLD_SERVICE=0
SYMLINK_SWITCHED=0
SWITCH_COMMITTED=0
restore_old_service_on_exit() {
  exit_code=$?
  trap - EXIT INT TERM
  cleanup_staged_archive
  if (( exit_code != 0 && SWITCH_COMMITTED == 0 )); then
    if (( SYMLINK_SWITCHED == 1 )); then
      if [[ -n ${OLD_TARGET} && -d ${OLD_TARGET} ]]; then
        ln -sfn "${OLD_TARGET}" /opt/golden-mic/current
        systemctl restart golden-mic.service || true
      else
        systemctl stop golden-mic.service || true
        rm -f /opt/golden-mic/current
      fi
    elif (( DRAINED_OLD_SERVICE == 1 )); then
      if systemctl is-active --quiet golden-mic.service; then
        curl --fail --silent --request DELETE \
          http://127.0.0.1:8000/api/admin/drain >/dev/null || true
      elif [[ -n ${OLD_TARGET} && -d ${OLD_TARGET} ]]; then
        ln -sfn "${OLD_TARGET}" /opt/golden-mic/current
        systemctl restart golden-mic.service || true
      fi
    fi
    if (( TARGET_CREATED == 1 )) && [[ -d ${TARGET} ]]; then
      rm -rf -- "${TARGET}"
    fi
  fi
  exit "${exit_code}"
}
trap restore_old_service_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if systemctl is-active --quiet golden-mic.service; then
  curl --fail --silent --show-error --request POST http://127.0.0.1:8000/api/admin/drain >/dev/null
  DRAINED_OLD_SERVICE=1
  deadline=$(( $(date +%s) + DRAIN_TIMEOUT ))
  while true; do
    active_tasks=$(curl --fail --silent http://127.0.0.1:8000/api/admin/drain | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["active_tasks"]))')
    if (( active_tasks == 0 )); then
      break
    fi
    if (( $(date +%s) >= deadline )); then
      curl --fail --silent --request DELETE http://127.0.0.1:8000/api/admin/drain >/dev/null || true
      echo "Drain timed out with ${active_tasks} active task(s); deployment aborted." >&2
      exit 1
    fi
    sleep 5
  done
fi

ln -sfn "${TARGET}" /opt/golden-mic/current
SYMLINK_SWITCHED=1
systemctl daemon-reload
if ! systemctl restart golden-mic.service; then
  START_FAILED=1
else
  START_FAILED=0
fi

if (( START_FAILED == 0 )); then
  ready=0
  for _ in $(seq 1 60); do
    if curl --fail --silent http://127.0.0.1:8000/health/ready >/dev/null; then
      ready=1
      break
    fi
    sleep 2
  done
  if (( ready == 0 )); then
    START_FAILED=1
  fi
fi

if (( START_FAILED != 0 )); then
  journalctl -u golden-mic.service --no-pager -n 100 >&2 || true
  if [[ -n ${OLD_TARGET} && -d ${OLD_TARGET} ]]; then
    ln -sfn "${OLD_TARGET}" /opt/golden-mic/current
    systemctl restart golden-mic.service || true
  else
    systemctl stop golden-mic.service || true
    rm -f /opt/golden-mic/current
  fi
  DRAINED_OLD_SERVICE=0
  echo "Deployment failed and the previous release was restored." >&2
  exit 1
fi

systemctl reload nginx
SWITCH_COMMITTED=1
trap - EXIT INT TERM
cleanup_staged_archive
printf 'release=%s target=%s previous=%s\n' "${RELEASE_ID}" "${TARGET}" "${OLD_TARGET:-none}"

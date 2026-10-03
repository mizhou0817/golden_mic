#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Target validation must run as root." >&2
  exit 1
fi
for command_name in curl grep mountpoint nginx stat systemctl systemd-analyze readlink runuser ss; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "Missing required command: ${command_name}" >&2
    exit 1
  }
done

mountpoint --quiet /srv/golden-mic-data
nginx -t
systemd-analyze verify /etc/systemd/system/golden-mic.service
systemctl is-active --quiet golden-mic.service

ENV_FILE=/etc/golden-mic/golden-mic.env
[[ $(stat -c '%a' "${ENV_FILE}") == "640" ]]
[[ $(stat -c '%U:%G' "${ENV_FILE}") == "root:goldenmic" ]]
[[ $(stat -c '%U:%G' /etc/golden-mic/release.pub) == "root:root" ]]

CURRENT=$(readlink -f /opt/golden-mic/current)
[[ -x ${CURRENT}/.venv/bin/python ]]
"${CURRENT}/.venv/bin/python" "${CURRENT}/deploy/verify_python_environment.py"
runuser -u goldenmic -- \
  "${CURRENT}/.venv/bin/python" "${CURRENT}/deploy/validate_environment_file.py" \
  /etc/golden-mic/golden-mic.env
runuser -u goldenmic -- python3 "${CURRENT}/deploy/run_with_environment.py" \
  --cwd /srv/golden-mic-data/tmp \
  --set PYTHONPATH="${CURRENT}" \
  --set HOME=/nonexistent \
  --set TMPDIR=/srv/golden-mic-data/tmp \
  /etc/golden-mic/golden-mic.env \
  -- "${CURRENT}/.venv/bin/python" -m backend.preflight

curl --fail --silent http://127.0.0.1:8000/health/live >/dev/null
curl --fail --silent http://127.0.0.1:8000/health/ready >/dev/null

# Production never grants local-workspace authority, even on direct loopback.
# Only public configuration is parsed; no session, task token or secret is read.
curl --fail --silent --show-error http://127.0.0.1:8000/api/config/workspace | \
  "${CURRENT}/.venv/bin/python" -c 'import json,sys; value=json.load(sys.stdin); sys.exit(0 if isinstance(value,dict) and value.get("local_history") is False and type(value.get("generative_fill_available")) is bool else 1)'
if [[ $(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  'http://127.0.0.1:8000/api/tasks?offset=0&limit=1') != "404" ]]; then
  echo "Production must not expose a global task index, including on loopback." >&2
  exit 1
fi
# No body/media/capability: this negative probe cannot start a paid task.
if [[ $(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  --request POST --header 'Content-Length: 0' http://127.0.0.1:8000/api/tasks) != "403" ]]; then
  echo "Production task creation must reject missing Origin/session/CSRF protection." >&2
  exit 1
fi

if curl --silent --fail http://127.0.0.1:8000/docs >/dev/null; then
  echo "Production docs endpoint must be disabled." >&2
  exit 1
fi
if curl --silent --fail http://127.0.0.1:8000/openapi.json >/dev/null; then
  echo "Production OpenAPI endpoint must be disabled." >&2
  exit 1
fi

if grep -R -E '(^|[?&])(token|auth_key|signature)=' \
  /var/log/nginx/golden-mic.access.log* \
  /var/log/nginx/golden-mic.error.log* >/dev/null 2>&1; then
  echo "Sensitive query material found in Nginx logs." >&2
  exit 1
fi

if ss -lntp | grep -E '0\.0\.0\.0:8000|\[::\]:8000' >/dev/null; then
  echo "Uvicorn port 8000 is exposed beyond loopback." >&2
  exit 1
fi
if ! ss -lntp | grep -E '127\.0\.0\.1:8000' >/dev/null; then
  echo "Uvicorn loopback listener is missing." >&2
  exit 1
fi

echo "target_host_validation=pass"
#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  sudo ./deploy/install_host.sh \
    --domain news.example.com \
    --certificate /etc/letsencrypt/live/news.example.com/fullchain.pem \
    --certificate-key /etc/letsencrypt/live/news.example.com/privkey.pem \
    --release-public-key /root/golden-mic-release.pub

Production has no application accounts or login setup. The application always
enforces signed anonymous browser sessions, CSRF + Origin checks, per-session/IP
quotas, and per-task capabilities. There is no production-wide task index.
EOF
}

require_root() {
  if [[ ${EUID} -ne 0 ]]; then
    echo "This installer must run as root." >&2
    exit 1
  fi
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

DOMAIN=""
CERTIFICATE=""
CERTIFICATE_KEY=""
RELEASE_PUBLIC_KEY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN=${2:-}; shift 2 ;;
    --certificate) CERTIFICATE=${2:-}; shift 2 ;;
    --certificate-key) CERTIFICATE_KEY=${2:-}; shift 2 ;;
    --release-public-key) RELEASE_PUBLIC_KEY=${2:-}; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

require_root
for command_name in nginx curl sed install getent useradd groupadd systemctl mktemp grep ln rm minisign mountpoint; do
  require_command "${command_name}"
done

if [[ ! ${DOMAIN} =~ ^[A-Za-z0-9.-]+$ ]] || [[ ${DOMAIN} == .* ]] || [[ ${DOMAIN} == *. ]]; then
  echo "Invalid DNS domain: ${DOMAIN}" >&2
  exit 1
fi
for path in "${CERTIFICATE}" "${CERTIFICATE_KEY}" "${RELEASE_PUBLIC_KEY}"; do
  if [[ -z ${path} || ! -s ${path} ]]; then
    echo "Required file is missing: ${path:-<empty>}" >&2
    exit 1
  fi
done
if ! grep -Eq '^RW[A-Za-z0-9+/=]+$' "${RELEASE_PUBLIC_KEY}"; then
  echo "The Minisign public key file is malformed." >&2
  exit 1
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ! -d /srv/golden-mic-data ]] || ! mountpoint --quiet /srv/golden-mic-data; then
  echo "/srv/golden-mic-data must already be a dedicated mounted CBS filesystem." >&2
  exit 1
fi
# Non-interactive OS identity for systemd/filesystem isolation, not an app user.
if ! getent group goldenmic >/dev/null; then
  groupadd --system goldenmic
fi
if ! getent passwd goldenmic >/dev/null; then
  useradd --system --gid goldenmic --home-dir /nonexistent --shell /usr/sbin/nologin goldenmic
fi

install -d -o root -g root -m 0755 /opt/golden-mic /opt/golden-mic/releases
install -d -o root -g goldenmic -m 0750 /etc/golden-mic
install -d -o goldenmic -g goldenmic -m 0750 \
  /srv/golden-mic-data/tasks \
  /srv/golden-mic-data/cache \
  /srv/golden-mic-data/cache/asr \
  /srv/golden-mic-data/uv-cache \
  /srv/golden-mic-data/tmp
install -d -o www-data -g www-data -m 0700 /srv/golden-mic-data/nginx-body
install -d -o www-data -g www-data -m 0755 /var/www/letsencrypt

if [[ ! -f /etc/golden-mic/golden-mic.env ]]; then
  install -o root -g goldenmic -m 0640 \
    "${SCRIPT_DIR}/golden-mic.env.production.example" \
    /etc/golden-mic/golden-mic.env
  echo "Created /etc/golden-mic/golden-mic.env; replace every CHANGE_ME value before deployment."
else
  echo "Existing production environment retained; reconcile its keys with the release schema before deployment."
fi
install -o root -g root -m 0644 "${RELEASE_PUBLIC_KEY}" /etc/golden-mic/release.pub

escape_sed() {
  printf '%s' "$1" | sed -e 's/[&|]/\\&/g'
}
DOMAIN_ESCAPED=$(escape_sed "${DOMAIN}")
CERTIFICATE_ESCAPED=$(escape_sed "${CERTIFICATE}")
CERTIFICATE_KEY_ESCAPED=$(escape_sed "${CERTIFICATE_KEY}")
TEMP_CONFIG=$(mktemp)
trap 'rm -f "${TEMP_CONFIG}"' EXIT
sed \
  -e "s|__DOMAIN__|${DOMAIN_ESCAPED}|g" \
  -e "s|__TLS_CERTIFICATE__|${CERTIFICATE_ESCAPED}|g" \
  -e "s|__TLS_CERTIFICATE_KEY__|${CERTIFICATE_KEY_ESCAPED}|g" \
  "${SCRIPT_DIR}/nginx/golden-mic.conf.template" > "${TEMP_CONFIG}"
if grep -q '__[A-Z_]*__' "${TEMP_CONFIG}"; then
  echo "Nginx template still contains unresolved placeholders." >&2
  exit 1
fi
TEMP_NGINX_ROOT=$(mktemp -d)
trap 'rm -f "${TEMP_CONFIG}"; rm -rf "${TEMP_NGINX_ROOT}"' EXIT
install -d -m 0755 "${TEMP_NGINX_ROOT}/conf"
install -m 0644 "${SCRIPT_DIR}/nginx/golden-mic-proxy.conf" \
  "${TEMP_NGINX_ROOT}/conf/golden-mic-proxy.conf"
sed "s|/etc/nginx/golden-mic-proxy.conf|${TEMP_NGINX_ROOT}/conf/golden-mic-proxy.conf|g" \
  "${TEMP_CONFIG}" > "${TEMP_NGINX_ROOT}/conf/site.conf"
cat > "${TEMP_NGINX_ROOT}/nginx.conf" <<EOF
pid ${TEMP_NGINX_ROOT}/nginx.pid;
error_log stderr;
events {}
http {
    include /etc/nginx/mime.types;
    include ${TEMP_NGINX_ROOT}/conf/site.conf;
}
EOF
nginx -t -c "${TEMP_NGINX_ROOT}/nginx.conf"

install -o root -g root -m 0644 \
  "${SCRIPT_DIR}/nginx/golden-mic-proxy.conf" \
  /etc/nginx/golden-mic-proxy.conf
install -o root -g root -m 0644 "${TEMP_CONFIG}" /etc/nginx/sites-available/golden-mic.conf
ln -sfn /etc/nginx/sites-available/golden-mic.conf /etc/nginx/sites-enabled/golden-mic.conf
rm -f /etc/nginx/sites-enabled/default
install -o root -g root -m 0644 \
  "${SCRIPT_DIR}/systemd/golden-mic.service" \
  /etc/systemd/system/golden-mic.service

nginx -t
systemctl daemon-reload
systemctl enable nginx golden-mic.service
systemctl reload nginx || systemctl restart nginx

cat <<EOF
Host bootstrap completed for ${DOMAIN}.
Next:
  1. Edit /etc/golden-mic/golden-mic.env and remove every CHANGE_ME value.
  2. Set a server-generated ANONYMOUS_SESSION_SECRET of at least 32 random bytes;
     keep strict HTTPS Origin/Host validation and all resource limits enabled.
  3. Confirm Tencent Cloud security groups expose only 80/443.
  4. Deploy a signed release with deploy_release.sh.
EOF

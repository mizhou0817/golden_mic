# Single-host container: one uvicorn worker, FFmpeg 7.1 from Debian 13 (same generation the test suite
# runs on; FFmpeg 5.1 measures some phone MP4s ~0.05 s longer and fails the source-clock check), hashed Python lock.
# Build context = a staging copy with backend/, frontend/dist/, vendor/, deploy/ and the lock files.
FROM python:3.11-slim-trixie

ARG APT_MIRROR=mirrors.tencent.com
ARG PIP_INDEX=https://mirrors.cloud.tencent.com/pypi/simple/

RUN sed -i "s#deb.debian.org#${APT_MIRROR}#g" /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-production.lock pyproject.toml ./
COPY vendor/ vendor/
RUN pip install --no-cache-dir --require-hashes --index-url "${PIP_INDEX}" -r requirements-production.lock

COPY backend/ backend/
COPY deploy/ deploy/
COPY frontend/dist/ frontend/dist/

RUN useradd --system --uid 990 --home-dir /nonexistent --shell /usr/sbin/nologin goldmic \
 && mkdir -p /data && chown goldmic:goldmic /data
USER goldmic
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/nonexistent TMPDIR=/data/tmp

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -fsS -H "Host: localhost" http://127.0.0.1:8000/health/live || exit 1
ENTRYPOINT ["tini", "--"]
CMD ["sh", "-c", "mkdir -p /data/tmp /data/tasks /data/cache/asr && exec python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log"]

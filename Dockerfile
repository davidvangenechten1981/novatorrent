# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    TZ=Europe/Brussels \
    NOVA_CONFIG_DIR=/config \
    NOVA_DEFAULT_SAVE_PATH=/data/downloads \
    NOVA_PORT=8080
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /config /data \
    && chown 1026:100 /config /data
COPY --from=build /install /usr/local
# Runs unprivileged by default; override with `user: "PUID:PGID"` in compose (no root entrypoint,
# so cap_drop: ALL and no-new-privileges keep working).
USER 1026:100
VOLUME ["/config", "/data"]
EXPOSE 8080 6881/tcp 6881/udp
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('NOVA_PORT','8080'), timeout=4)"]
CMD ["python", "-m", "novatorrent"]

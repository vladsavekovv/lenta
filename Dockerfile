# syntax=docker/dockerfile:1
#
# LENTA in a container.
#
# The bare-metal installer (deploy/install.sh) and this file agree on one rule that matters more than
# any other: LENTA never installs, removes or changes a graphics driver. It uses the one already on the
# machine. In a container that means the same thing it means on the host — FFmpeg is a self-contained
# static build that loads whatever driver the container is given at run time, and nothing here pulls in
# a single NVIDIA, Mesa or VA-API package. Hardware transcoding comes from the host, through
# --gpus all (NVIDIA) or a passed-in /dev/dri (Intel and AMD). See docs/DOCKER.md.
#
# Two stages: everything that needs the network or a package manager happens in the first, and the
# image that ships is a Python base, a virtual environment, FFmpeg, and LENTA itself.

ARG PYTHON_VERSION=3.12

# ---- build: the virtual environment and FFmpeg -----------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS build
ARG TARGETARCH
# The same static GPL build the bare-metal installer uses, so a container and a host install transcode
# through identical code. It is built for x86_64 only; on arm64 the distribution's own FFmpeg is used
# instead (those machines have no NVENC to reach anyway).
ARG FFMPEG_URL=https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates xz-utils \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    mkdir -p /opt/ffmpeg; \
    if [ "${TARGETARCH}" = "amd64" ]; then \
      curl -fsSL "${FFMPEG_URL}" -o /tmp/ffmpeg.tar.xz; \
      tar -xJf /tmp/ffmpeg.tar.xz -C /tmp; \
      find /tmp -type f -name ffmpeg  -exec cp {} /opt/ffmpeg/ffmpeg  \; ; \
      find /tmp -type f -name ffprobe -exec cp {} /opt/ffmpeg/ffprobe \; ; \
      rm -rf /tmp/ffmpeg.tar.xz; \
    else \
      apt-get update; \
      apt-get install -y --no-install-recommends ffmpeg; \
      cp "$(command -v ffmpeg)"  /opt/ffmpeg/ffmpeg; \
      cp "$(command -v ffprobe)" /opt/ffmpeg/ffprobe; \
      rm -rf /var/lib/apt/lists/*; \
    fi; \
    chmod +x /opt/ffmpeg/ffmpeg /opt/ffmpeg/ffprobe; \
    /opt/ffmpeg/ffmpeg -version | head -1

COPY server/requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

# The Samsung TV package, built here exactly as deploy/install.sh builds it on a host, so the TV app is
# offered for download from a container too. One line rather than a heredoc: heredocs in a RUN need a
# particular BuildKit frontend, and a Dockerfile that only builds on some builders is worse than a
# dense line that builds on all of them.
COPY tizen /src/tizen
RUN mkdir -p /out/download \
 && python -c "import os,zipfile;src='/src/tizen';out='/out/download/lenta-tizen.wgt';z=zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED);[z.write(os.path.join(r,f),os.path.relpath(os.path.join(r,f),src)) for r,_,fs in os.walk(src) for f in fs];z.close()" \
 && test -s /out/download/lenta-tizen.wgt

# ---- runtime ---------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

LABEL org.opencontainers.image.title="LENTA Media Server" \
      org.opencontainers.image.description="Self-hosted media server: your own films, shows, music and live TV, on every screen in the house." \
      org.opencontainers.image.source="https://github.com/vladsavekovv/lenta" \
      org.opencontainers.image.licenses="Unlicense"

# util-linux carries setpriv, which the entrypoint uses to drop from root to the service account when
# the container is started as root (Unraid and Synology do). Nothing else is added.
RUN apt-get update \
 && apt-get install -y --no-install-recommends util-linux \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 1000 lenta \
 && useradd -u 1000 -g 1000 -M -d /data -s /usr/sbin/nologin lenta

COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/ffmpeg /opt/ffmpeg
COPY --chown=root:root server /app/server
COPY --chown=root:root web /app/web
COPY --chown=root:root tizen /app/tizen
COPY --chown=root:root deploy /app/deploy
COPY --chown=root:root docs /app/docs
COPY --chown=root:root README.md CHANGELOG.md LICENSE THIRD-PARTY-NOTICES.md /app/
COPY --from=build /out/download/lenta-tizen.wgt /app/web/download/lenta-tizen.wgt
COPY docker/entrypoint.sh /usr/local/bin/lenta-entrypoint

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LENTA_DATA_DIR=/data \
    LENTA_FFMPEG=/opt/ffmpeg/ffmpeg \
    LENTA_FFPROBE=/opt/ffmpeg/ffprobe \
    LENTA_HOST=0.0.0.0 \
    LENTA_PORT=8484

RUN chmod +x /usr/local/bin/lenta-entrypoint && mkdir -p /data && chown lenta:lenta /data

# The database, artwork, downloaded subtitles and the transcoding scratch space. Everything that must
# outlive the container is under here and nothing else is written to.
VOLUME ["/data"]

# 8484 plain HTTP; 8443 HTTPS, when it is switched on in Settings › Network.
EXPOSE 8484 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('LENTA_PORT','8484')+'/api/server',timeout=4)"]

WORKDIR /app/server

# No USER line, and that is deliberate. The container starts as root so the entrypoint can give /data to
# the account that owns the media on the host (PUID/PGID — Unraid and Synology rarely use 1000) and then
# drop to it with setpriv. Root gets no further than that script: the server itself runs as the account,
# and PID 1 inside a running container is the server, not a shell. To skip the root step entirely, start
# the container as a user — `user: "1000:1000"` in compose — and the entrypoint hands straight over.
ENTRYPOINT ["/usr/local/bin/lenta-entrypoint"]
CMD ["python", "-m", "lenta"]

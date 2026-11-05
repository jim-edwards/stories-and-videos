# The base image is pinned by digest so builds are repeatable; Dependabot opens a pull request
# whenever a newer one is published.
FROM python:3.13-alpine@sha256:7984cf56ceaddebe4fefeb75d64000b9f45d8b77b11a09b24524da6cdc3af0c8

LABEL org.opencontainers.image.title="Stories and Videos" \
      org.opencontainers.image.description="A simple, large-print video library for family stories." \
      org.opencontainers.image.licenses="GPL-3.0-only"

# `apk upgrade` pulls in security fixes published since the base image was built. ffprobe (part of
# the ffmpeg package) reads each video's title and description. tzdata lets TZ name a time zone.
# Package versions are deliberately not pinned here, so every rebuild picks up fixes.
# hadolint ignore=DL3018
RUN apk upgrade --no-cache \
 && apk add --no-cache ffmpeg tzdata \
 && addgroup -g 568 -S apps && adduser -u 568 -S -G apps apps \
 && mkdir -p /data /videos && chown apps:apps /data

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIDEO_DIR=/videos \
    DATA_DIR=/data

WORKDIR /srv
COPY requirements.txt .
# pip is only needed for this install. Removing it afterwards keeps its bundled libraries (which
# vulnerability scanners flag, and which the app never uses) out of the final image.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
 && pip uninstall -y pip

COPY LICENSE /usr/share/licenses/stories-and-videos/LICENSE
COPY app ./app
# Optional helper: `make-thumbnails /videos` saves a picture next to each video that lacks one.
COPY scripts/make-thumbnails.sh /usr/local/bin/make-thumbnails

# Runs as a fixed unprivileged user (568). Override with `user:` / `--user` if your video folder
# belongs to someone else; the container works as any user id.
USER 568:568
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"]

# One worker on purpose: it owns the background folder scanner. Threads serve many viewers at once.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "16", \
     "--timeout", "120", "--no-control-socket", "--access-logfile", "-", "app:create_app()"]

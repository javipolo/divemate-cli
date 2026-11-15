# Build:  podman build -t divemate-cli .
# Run:    see the dmcli wrapper script

FROM python:3.12-slim

WORKDIR /app

# Install the package. Copy only what pip needs so the image layer caches well
# and the (large) .ddb is never baked in (also excluded via .containerignore).
COPY pyproject.toml README.md ./
COPY divemate ./divemate
# include the gdrive extra (`dmcli pull`); charts (plotext) are a core dependency
RUN pip install --no-cache-dir '.[gdrive]'

# Run as a non-root user (defense-in-depth). The dmcli wrapper additionally uses
# `--userns=keep-id`, which maps the host user onto this process at runtime so the
# bind-mounted DB keeps the right ownership for both reads and `pull` writes.
RUN useradd --create-home --uid 1000 app
USER app

# The wrapper mounts the host DB here read-only.
ENV DIVEMATE_DB=/data/DiveMate.ddb

ENTRYPOINT ["dmcli"]

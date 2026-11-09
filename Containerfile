# Build:  podman build -t divemate-cli .
# Run:    see the dmcli wrapper script

FROM python:3.12-slim

WORKDIR /app

# Install the package. Copy only what pip needs so the image layer caches well
# and the (large) .ddb is never baked in (also excluded via .containerignore).
COPY pyproject.toml README.md ./
COPY divemate ./divemate
# include gdrive (`dmcli pull`) and charts (`info --profile`) extras
RUN pip install --no-cache-dir '.[gdrive,charts]'

# The wrapper mounts the host DB here read-only.
ENV DIVEMATE_DB=/data/DiveMate.ddb

ENTRYPOINT ["dmcli"]

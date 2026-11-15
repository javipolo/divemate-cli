# Build:  podman build -t divemate-cli .
# Run:    see the dmcli wrapper script

FROM python:3.12-slim

# --- stable layers first (rarely change -> stay cached across source edits) ---

# Non-root user (defense-in-depth). The dmcli wrapper additionally uses
# `--userns=keep-id`, which maps the host user onto this process at runtime so the
# bind-mounted DB keeps the right ownership for both reads and `pull` writes.
RUN useradd --create-home --uid 1000 app

WORKDIR /app

# The wrapper mounts the host DB here read-only.
ENV DIVEMATE_DB=/data/DiveMate.ddb

# --- dependency layer: cached unless pyproject.toml changes -------------------

# Install only the third-party deps, resolved from the real pyproject (no drift)
# but with a stub package so this layer does NOT depend on the source tree. The
# (large) .ddb is never baked in (also excluded via .containerignore).
COPY pyproject.toml README.md ./
RUN mkdir -p divemate && touch divemate/__init__.py \
    && pip install --no-cache-dir '.[gdrive]' \
    && rm -rf divemate

# --- app layer: rebuilt only when the source changes -------------------------

# Install the package itself without touching the already-installed deps.
COPY divemate ./divemate
RUN pip install --no-cache-dir --no-deps .

USER app

ENTRYPOINT ["dmcli"]

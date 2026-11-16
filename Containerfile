# Build:  podman build -t divemate-cli .
# Run:    see the dmcli wrapper script

FROM python:3.12-slim

# --- stable layers first (rarely change -> stay cached across source edits) ---

# No `USER` directive on purpose: the dmcli wrapper runs with `--userns=keep-id`,
# which maps the host user onto the container process. That already runs as a
# non-root uid (your host user) AND gives the bind-mounted DB/key the right owner
# for both reads and `pull` writes. Pinning USER to a fixed uid here instead
# breaks those mounts whenever the host uid differs from it.

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

ENTRYPOINT ["dmcli"]

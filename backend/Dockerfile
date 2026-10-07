# backend/Dockerfile — Life OS API.
#
# Base images are DIGEST-PINNED, not tag-pinned. A tag like `python:3.12-slim`
# is mutable: whoever controls the registry can change what "3.12-slim" points at
# between this commit's CI run and the next one, and this image is what runs the
# code that touches the user's financial data. The digest makes the build
# reproducible — same input, same bytes, forever.
#
# Refresh with:
#     docker buildx imagetools inspect python:3.12-slim-bookworm
# and update the digest deliberately, in a commit that says so.

# ---------------------------------------------------------------------------
# builder — resolve and install dependencies with uv, using the committed lockfile
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.8@sha256:3b7b60a81d3c57ef471703e5c83fd4aaa33abcd403596fb22ab07db85ae91347 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# --frozen means "fail if uv.lock does not satisfy pyproject.toml". That is what
# makes the dependency gate in CI meaningful: the lockfile is the contract, and
# this build refuses to silently resolve a different set of packages.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# The project itself. backend/ is the import root, so `core` and `finance`
# install as top-level packages (see the note at the top of pyproject.toml).
# --no-editable: a runtime image wants a real installed copy, not a .pth file
# pointing back at a source directory that nothing else is going to edit.
COPY backend/ ./backend/
# `uv cache clean lifeos` BEFORE the sync. Why: the wheel cache is keyed on the
# project's name+version metadata, NOT on the source tree — so a backend code
# change without a version bump lets this cache-mount rebuild serve a STALE
# wheel (observed 2026-10-07: a freshly rebuilt image whose runtime served
# `/api/v1/budgets` as 404 while `/app/backend` contained the working code).
# Cleaning just the project's artefacts leaves every dependency cached.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv cache clean lifeos \
 && uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------------------
# runtime — no compilers, no dev dependencies, no network tools
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS runtime

# libpq5 and poppler-utils.
#
# ADR 0004 moved pyproject.toml to `psycopg[binary]`, which bundles its own
# libpq, so the libpq5 install is no longer load-bearing — the wheel works
# without it. It is kept deliberately: it is a few hundred KB, and the image
# keeps working if the pin is ever reverted to bare `psycopg`, where psycopg 3
# still binds to the system libpq and the import otherwise fails at first use
# with "no pq wrapper available". Drop this layer if image size starts to
# matter.
#
# poppler-utils IS load-bearing. `pdftotext -layout` is the text extraction the
# three PDF adapters share (finance/ingestion/adapters/pdf_text.py), and it is
# a system binary, not a pip package. Without this line every PDF upload fails
# in the container with "pdftotext is not installed" while passing on a host
# that happens to have poppler. The test suite cannot catch that, because it
# exercises the host's binary rather than the image's.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends libpq5 poppler-utils \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# A fixed, high, unprivileged uid/gid. Not 1000: on a shared host 1000 is
# usually a human, and "lifeos" running as the invoking user by accident is
# exactly the sort of quiet privilege bleed this image is trying to avoid.
RUN groupadd --system --gid 10001 lifeos \
 && useradd --system --uid 10001 --gid lifeos --no-create-home --shell /usr/sbin/nologin lifeos

WORKDIR /app

COPY --from=builder --chown=lifeos:lifeos /app/.venv /app/.venv
COPY --from=builder --chown=lifeos:lifeos /app/backend /app/backend

USER lifeos

EXPOSE 8000

# Not the `lifeos` console script. That entry point is `main:app`, a FastAPI
# *instance*; a console script wraps its target in sys.exit(target()), which
# would try to call the app object and raise TypeError. uvicorn is the actual
# ASGI server, so it is what runs.
#
# `--proxy-headers` is off: this app is reached over loopback or Tailscale only
# and trusting forwarded headers it does not need is a header-spoofing surface.
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--no-access-log"]
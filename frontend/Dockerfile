# frontend/Dockerfile — Life OS web UI.
#
# Two constraints from P2 drive this file, and both are easy to get wrong:
#
# 1. Assets are referenced with ABSOLUTE paths (`/assets/index-XXXX.js`), so
#    `dist/` must be served at the server root. Building with a `base: '/app/'`
#    and serving from a subpath produces a page whose script 404s. Nothing here
#    sets a Vite `base`, so the root default applies.
#
# 2. `VITE_API_BASE_URL` is BAKED INTO THE BUNDLE AT BUILD TIME. Vite replaces
#    `import.meta.env.VITE_API_BASE_URL` with a literal during `vite build`, so
#    this is a build ARG, not a runtime environment variable. Passing it at
#    `docker run` time changes nothing: the container only serves files that
#    were already built. To point the UI at a different API, rebuild:
#
#        docker build -f frontend/Dockerfile \
#            --build-arg VITE_API_BASE_URL=https://api.example.test/v1 \
#            --build-arg VITE_API_BASE_URL_SHA=<sha256 of that value> .
#
#    VITE_API_BASE_URL_SHA is required whenever VITE_API_BASE_URL is overridden.
#    Without it, two builds of the same source produce different bundles and
#    Docker's build cache happily serves the wrong one — a cache-key collision
#    that silently ships the previous API URL to production. The digest forces
#    the cache to key on the actual value.

# ---------------------------------------------------------------------------
# build — node compiles and bundles; nothing from this stage ships except dist/
# ---------------------------------------------------------------------------
FROM node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS build

# /api — root-relative, so no host is baked in. The compose stack proxies /api
# to the api service in nginx (see docker-compose.yml).
ARG VITE_API_BASE_URL=/api

# The cache key. Derived from VITE_API_BASE_URL by the build arg default when
# unset, so the common case needs no second flag. See the note above for why the
# runtime environment cannot do this job instead.
ARG VITE_API_BASE_URL_SHA=

WORKDIR /build

# `npm ci`, never `npm install`: ci installs exactly what package-lock.json
# pins and fails if package.json and the lockfile disagree, where install would
# quietly resolve something new. That is the same property --frozen gives uv.
COPY package.json package-lock.json ./

# Dev dependencies ARE required here: `npm run build` is `tsc -b && vite build`,
# and tsc is a devDependency. Omitting them would silently skip type checking.
#
# Only the npm download cache is mounted. A cache mount over
# node_modules/.vite would be faster on repeat builds, but `npm ci` deletes
# node_modules wholesale on entry and fails with EBUSY on the mounted directory.
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts vitest.config.ts ./
COPY index.html ./
COPY public/ ./public/
COPY src/ ./src/

ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# `tsc -b`, not bare `tsc --noEmit`. tsconfig.json here is solution-style
# (`"files": []` plus project references), so a bare `tsc --noEmit` type-checks
# NOTHING and exits 0 — a green check that verified zero files. `tsc -b` walks
# the references and actually checks tsconfig.app.json and tsconfig.node.json.
RUN test -n "$VITE_API_BASE_URL_SHA" \
        && echo "VITE_API_BASE_URL_SHA is required when overriding VITE_API_BASE_URL" >&2 \
        && exit 1 \
    ; npm run build

# Fail loudly rather than shipping an empty image: a dist/ that never got
# index.html means the build silently no-op'd.
RUN test -s /build/dist/index.html

# ---------------------------------------------------------------------------
# runtime — static files only
# ---------------------------------------------------------------------------
FROM nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6 AS runtime

LABEL org.opencontainers.image.title="lifeos-frontend"

# nginx-unprivileged ships a default server block that serves
# /usr/share/nginx/html but has no SPA fallback, so a hard refresh on
# /transactions would 404. Replace it at build time rather than mounting a
# config at run time: a runtime mount would have to be attached by whatever
# deploys the image, it is incompatible with a read-only root filesystem, and it
# leaves the image useless on its own. `docker build` alone produces a working
# server.
#
# The heredoc delimiter is quoted so the shell does not expand nginx's own
# runtime variables — $uri, $host, $scheme and friends must survive verbatim
# into the config file.
#
# Written as root because the USER switch below comes after; the served files
# are then chowned to nginx, which is the only identity that ever reads them.
RUN cat > /etc/nginx/conf.d/default.conf <<'NGINX'
server {
    listen 8080;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    # Hashed filenames: safe to cache forever.
    location /assets/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files $uri =404;
    }

    # SPA fallback. A client-side route like /transactions must return
    # index.html so the router can resolve it. try_files falling through to
    # /index.html means a missing asset under /assets/ is still a real 404,
    # which is a different failure and should look like one.
    location / {
        add_header Cache-Control "no-cache";
        try_files $uri $uri/ /index.html;
    }

    location = /healthz {
        access_log off;
        add_header Content-Type text/plain;
        return 200 "ok\n";
    }

    # Same-origin proxy to the API, so the browser never needs CORS and the
    # bundle needs no hostname baked into it.
    location /api/ {
        proxy_pass http://api:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # A 7-year Amex PDF import is a large upload on a slow link.
        client_max_body_size 64m;
        proxy_read_timeout 300s;
    }

    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;
    gzip_min_length 1024;
}
NGINX

# test -s guards the case where the heredoc silently wrote nothing, which would
# otherwise leave the image serving its stock config with no SPA routing.
#
# `nginx -t` is deliberately NOT run here. nginx resolves an upstream given as a
# literal hostname while it loads the configuration, so validating at build time
# fails with "host not found in upstream api" — there is no compose network yet.
# The two ways around it are both worse than not doing it:
#   - `proxy_pass $variable` defers the lookup to request time, but then nginx
#     needs an explicit `resolver` directive, and any address hardcoded there
#     (127.0.0.11 is Docker's embedded DNS) makes the image compose-only. It also
#     silently returns 502 on a name it cannot resolve, which is a worse failure
#     than a build error.
#   - seeding /etc/hosts to satisfy the check would shadow real DNS for `api` at
#     runtime.
# The config is instead exercised by the container healthcheck and by `make up`,
# which both fail loudly.
RUN test -s /etc/nginx/conf.d/default.conf

COPY --from=build --chown=nginx:nginx /build/dist/ /usr/share/nginx/html/

# The image's own nginx already runs as uid 101, writes its pid and temp dirs
# under /tmp, and listens on 8080 — no root, no CAP_NET_BIND_SERVICE. That is
# the entire reason this base is used instead of nginx:latest, whose master
# process binds :80 as root and forks workers.
USER nginx:nginx

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1:8080/healthz || exit 1
# TRATT (continued development of OCTRA) — https://github.com/humlab-speech/TRATT
#
# Build context: external/TRATT, checked out by './visp.py deploy update' at the
# commit release.json pins (components.TRATT). Ignore rules:
# Dockerfile.dockerignore next to this file (passed via --ignorefile).
#
# Dev mode does not use the app baked in here: the tratt-build container
# watch-builds external/TRATT and the dev quadlet mounts that output over
# htdocs (see quadlets/dev/tratt.container).

# Stage 1: Build the project
FROM docker.io/library/node:24.22.2 AS build

WORKDIR /app

# git: scripts/generate-build-info.mjs stamps the commit into the navbar's
# version popover ('dev' when .git is absent).
RUN apt-get update && \
    apt-get install -y git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Dependencies first, for layer caching. The postinstall hook runs
# scripts/generate-build-info.mjs, so that one script comes along.
#
# --legacy-peer-deps: the package-lock.json carries non-optional peer deps on
# react/react-dom/react-refresh (from @module-federation/data-prefetch and
# @rspack/plugin-react-refresh) without lockfile entries for them, so plain
# `npm ci` aborts with "Missing: react@... from lock file". TRATT is Angular;
# those React peers are build-tooling artefacts and are not needed. The flag
# keeps the install exact-from-lockfile, it only stops npm auto-resolving the
# unlisted peers against the live registry.
COPY package.json package-lock.json ./
COPY scripts/generate-build-info.mjs scripts/
RUN npm ci --legacy-peer-deps

COPY . .

# Build the release: production build that strips the bundled appconfig.json,
# so the deployment's config (mounts/tratt/appconfig.json, aliased in
# mounts/tratt/httpd.conf) is the single source of truth.
RUN npm run build:release

# Stage 2: Serve the built files with Apache HTTPD
FROM docker.io/library/httpd:2.4.69

# The httpd tag pins Apache itself; the Debian packages underneath are only as
# new as the tag's build date. Upgrade them so a --no-cache rebuild picks up
# every Debian security fix (openssl, perl, …) whatever the tag's age.
RUN apt-get update && \
    apt-get upgrade -y --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Copy the built app. Deployment-owned files (appconfig.json, the demo audio)
# are mounted at /usr/local/apache2/visp and aliased in httpd.conf.
COPY --from=build /app/dist/apps/tratt /usr/local/apache2/htdocs/

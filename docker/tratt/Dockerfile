# Stage 1: Build the project
FROM docker.io/library/node:24.22.2 AS build

WORKDIR /app

# Install git and clean up afterwards
RUN apt-get update && \
    apt-get install -y git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Clone TRATT (continued development of OCTRA), pinned to a specific commit
RUN git clone --branch humlab-3.3.2 --depth 1 https://github.com/humlab-speech/TRATT.git . && \
    git checkout b4c4b2cadcafcb701b9ee6847c350757f2f98821

# Install dependencies (ci = exact lockfile, reproducible build).
# --legacy-peer-deps: upstream's package-lock.json carries non-optional peer
# deps on react/react-dom/react-refresh (from @module-federation/data-prefetch
# and @rspack/plugin-react-refresh) without lockfile entries for them, so plain
# `npm ci` aborts with "Missing: react@... from lock file". TRATT is Angular;
# those React peers are build-tooling artefacts and are not needed. The flag
# keeps the install exact-from-lockfile, it only stops npm auto-resolving the
# unlisted peers against the live registry.
RUN npm ci --legacy-peer-deps

# Shim for TRATT build.js: it execs ./node_modules/nx/bin/nx.js, but nx@22 moved
# its entry to dist/bin/nx.js (upstream bug: TRATT CI never runs the release build).
# The existence guard keeps this inert on nx <22 (e.g. TRATT 3.0.1's nx 20.8.0,
# where bin/nx.js exists): without it the ln -sf would replace that real file
# with a dangling link and break a build that works today.
RUN if [ ! -e node_modules/nx/bin/nx.js ]; then mkdir -p node_modules/nx/bin && ln -s ../dist/bin/nx.js node_modules/nx/bin/nx.js; fi

# Build the release: production build that strips the bundled appconfig.json,
# so the deployment's mounted config (mounts/tratt/appconfig.json) is the
# single source of truth.
RUN npm run build:release

# Stage 2: Serve the built files with Apache HTTPD
FROM docker.io/library/httpd:2.4.69

# The httpd tag pins Apache itself; the Debian packages underneath are only as
# new as the tag's build date. Upgrade them so a --no-cache rebuild picks up
# every Debian security fix (openssl, perl, …) whatever the tag's age.
RUN apt-get update && \
    apt-get upgrade -y --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Create the media directory
RUN mkdir -p /usr/local/apache2/htdocs/media

# Copy the built app
COPY --from=build /app/dist/apps/tratt /usr/local/apache2/htdocs/

# The app is built with --deploy-url=/assets/ so hashed fonts are requested as
# /assets/media/*.woff*, but the build emits them under dist .../media/. Without
# this link the SPA fallback answers font requests with index.html and Chromium
# logs "downloadable font: rejected by sanitizer" (silently degrading to fallback fonts).
RUN ln -s ../media /usr/local/apache2/htdocs/assets/media

# Copy the demo file
COPY demo_file.wav /usr/local/apache2/htdocs/media/demo_file.wav

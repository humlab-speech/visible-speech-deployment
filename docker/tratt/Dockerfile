# Stage 1: Build the project
FROM docker.io/node:24.15.0 AS build

WORKDIR /app

# Install git and clean up afterwards
RUN apt-get update && \
    apt-get install -y git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Clone TRATT (continued development of OCTRA), pinned to a specific commit
RUN git clone --branch humlab-3.0.1 --depth 1 https://github.com/humlab-speech/TRATT.git . && \
    git checkout 59f9c1f7777ab68e92e10e88ef20cb005ee8b3a4

# Install dependencies (ci = exact lockfile, reproducible build).
# --legacy-peer-deps: upstream's package-lock.json carries non-optional peer
# deps on react/react-dom/react-refresh (from @module-federation/data-prefetch
# and @rspack/plugin-react-refresh) without lockfile entries for them, so plain
# `npm ci` aborts with "Missing: react@... from lock file". TRATT is Angular;
# those React peers are build-tooling artefacts and are not needed. The flag
# keeps the install exact-from-lockfile, it only stops npm auto-resolving the
# unlisted peers against the live registry.
RUN npm ci --legacy-peer-deps

# Build the release: production build that strips the bundled appconfig.json,
# so the deployment's mounted config (mounts/tratt/appconfig.json) is the
# single source of truth.
RUN npm run build:release

# Stage 2: Serve the built files with Apache HTTPD
FROM docker.io/httpd:2.4.67

# Create the media directory
RUN mkdir -p /usr/local/apache2/htdocs/media

# Copy the built app
COPY --from=build /app/dist/apps/tratt /usr/local/apache2/htdocs/

# Copy the demo file
COPY demo_file.wav /usr/local/apache2/htdocs/media/demo_file.wav

FROM docker.io/library/node:24.22.2-alpine3.24

LABEL maintainer="Visible Speech <visp@umu.se>"
LABEL description="Body-inspecting Podman socket proxy for session-manager. Enforces image allowlist, mount path allowlist, and capability allowlist on container create calls."

# Patch the Alpine packages past the tag's build date, and drop npm and yarn:
# the proxy has no dependencies, and their bundled node_modules are only attack
# surface (and scanner findings).
RUN apk upgrade --no-cache && \
    rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx \
           /opt/yarn-* /usr/local/bin/yarn /usr/local/bin/yarnpkg

WORKDIR /app
COPY proxy.js .

# No npm deps — uses only Node built-ins (http, net, fs, path).
# Runs as root inside the container so it can connect to the host Podman socket
# (which is owned by the host user = UID 0 inside the rootless user namespace).
# The proxy container itself has no capabilities and no network — running as
# container-root here is safe and does not grant host privileges.

ENTRYPOINT ["node", "/app/proxy.js"]

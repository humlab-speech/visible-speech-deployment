FROM docker.io/node:22.22.3-alpine3.22

LABEL maintainer="Visible Speech <visp@umu.se>"
LABEL description="Body-inspecting Podman socket proxy for session-manager. Enforces image allowlist, mount path allowlist, and capability allowlist on container create calls."

WORKDIR /app
COPY proxy.js .

# No npm deps — uses only Node built-ins (http, net, fs, path).
# Runs as root inside the container so it can connect to the host Podman socket
# (which is owned by the host user = UID 0 inside the rootless user namespace).
# The proxy container itself has no capabilities and no network — running as
# container-root here is safe and does not grant host privileges.

ENTRYPOINT ["node", "/app/proxy.js"]

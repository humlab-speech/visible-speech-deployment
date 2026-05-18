#!/usr/bin/env node
/**
 * VISP Podman Socket Proxy
 *
 * Sits between session-manager and the real Podman socket. Passes all requests
 * through transparently except POST .../containers/create, which is inspected
 * and rejected if it violates the allowlist policy.
 *
 * Policy enforced on container create:
 *   1. image must match /^(localhost\/visp-|docker\.io\/library\/alpine)/
 *   2. privileged must be absent or false
 *   3. Every mount source must be under ABS_ROOT_PATH
 *   4. cap_add may only contain caps from CAP_ALLOWLIST
 *   5. netns.nsmode must be "none" or "bridge" (no host networking)
 *   6. No host_pid / host_ipc / host_uts
 *
 * Passes through unchanged:
 *   - GET  /containers/json
 *   - GET  /images/json
 *   - GET  /containers/{id}/json
 *   - POST /containers/{id}/exec    (exec into already-running container)
 *   - POST /exec/{id}/start
 *   - POST /libpod/containers/{id}/start
 *   - POST /libpod/containers/{id}/stop
 *   - Everything else read-only
 *
 * Usage:
 *   PODMAN_SOCKET=/run/user/1000/podman/podman.sock \
 *   PROXY_SOCKET=/run/podman-proxy/podman.sock \
 *   ABS_ROOT_PATH=/home/tomas/Projects/visible-speech-deployment \
 *   node proxy.js
 */

"use strict";

const http = require("http");
const net = require("net");
const fs = require("fs");
const path = require("path");

// ── Config ────────────────────────────────────────────────────────────────────

const PODMAN_SOCKET = process.env.PODMAN_SOCKET || "/run/user/1000/podman/podman.sock";
const PROXY_SOCKET  = process.env.PROXY_SOCKET  || "/run/podman-proxy/podman.sock";
const ABS_ROOT_PATH = process.env.ABS_ROOT_PATH || "";
const LOG_LEVEL     = (process.env.LOG_LEVEL || "info").toLowerCase();

// Capabilities that session containers are allowed to add back.
// Must match the securityProfiles in Session.class.js.
const CAP_ALLOWLIST = new Set([
    "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID",
    "SETGID", "SETUID", "SETPCAP",
    // Required by visp-session-proxy (tinyproxy sidecar) so its entrypoint.sh
    // can install nftables OUTPUT rules that block traffic to private/internal
    // subnets by IP. Scoped to the container's own network namespace only.
    "NET_ADMIN",
]);

// Images that session-manager is allowed to spawn.
const IMAGE_PATTERN = /^(localhost\/visp-|docker\.io\/library\/alpine)/;

// ── Logging ───────────────────────────────────────────────────────────────────

function log(level, msg, extra) {
    const levels = { debug: 0, info: 1, warn: 2, error: 3 };
    if ((levels[level] ?? 1) < (levels[LOG_LEVEL] ?? 1)) return;
    const ts = new Date().toISOString();
    const line = extra !== undefined
        ? `[${ts}] [podman-proxy] [${level.toUpperCase()}] ${msg} ${JSON.stringify(extra)}`
        : `[${ts}] [podman-proxy] [${level.toUpperCase()}] ${msg}`;
    (level === "error" || level === "warn" ? process.stderr : process.stdout).write(line + "\n");
}

// ── Policy check ──────────────────────────────────────────────────────────────

/**
 * Validate a libpod or Docker-compat container create body.
 * Returns null if OK, or a human-readable rejection reason string.
 */
function checkCreateBody(body) {
    let spec;
    try {
        spec = JSON.parse(body);
    } catch (e) {
        return `Invalid JSON body: ${e.message}`;
    }

    // 1. Image allowlist
    const image = spec.image || spec.Image || "";
    if (!image) return "Missing image field";
    if (!IMAGE_PATTERN.test(image)) {
        return `Image "${image}" is not in the allowlist (must match ${IMAGE_PATTERN})`;
    }

    // 2. No privileged
    const privileged =
        spec.privileged ||                          // libpod SpecGenerator
        (spec.HostConfig && spec.HostConfig.Privileged); // Docker-compat
    if (privileged) return "Privileged containers are not allowed";

    // 3. Mount sources must be under ABS_ROOT_PATH
    if (ABS_ROOT_PATH) {
        const mounts = spec.mounts ||               // libpod SpecGenerator
            (spec.HostConfig && spec.HostConfig.Mounts) || [];
        // Also check Docker-compat Binds (host:container strings)
        const binds = (spec.HostConfig && spec.HostConfig.Binds) || [];

        for (const m of mounts) {
            const src = m.source || m.Source || "";
            if (!src) continue;
            const resolved = path.resolve(src);
            if (!resolved.startsWith(path.resolve(ABS_ROOT_PATH) + path.sep) &&
                resolved !== path.resolve(ABS_ROOT_PATH)) {
                return `Mount source "${src}" is outside ABS_ROOT_PATH ("${ABS_ROOT_PATH}")`;
            }
        }
        for (const bind of binds) {
            const src = bind.split(":")[0];
            if (!src) continue;
            const resolved = path.resolve(src);
            if (!resolved.startsWith(path.resolve(ABS_ROOT_PATH) + path.sep) &&
                resolved !== path.resolve(ABS_ROOT_PATH)) {
                return `Bind source "${src}" is outside ABS_ROOT_PATH ("${ABS_ROOT_PATH}")`;
            }
        }
    }

    // 4. cap_add allowlist
    const capAdd =
        spec.cap_add ||                             // libpod SpecGenerator
        (spec.HostConfig && spec.HostConfig.CapAdd) || [];
    for (const cap of capAdd) {
        const normalised = cap.replace(/^CAP_/, "").toUpperCase();
        if (!CAP_ALLOWLIST.has(normalised)) {
            return `Capability "${cap}" is not in the allowlist`;
        }
    }

    // 5. Network mode: only "none" or named bridge network (no "host")
    const netns = spec.netns;           // libpod: { nsmode: "none"|"bridge" }
    const networkMode = spec.HostConfig && spec.HostConfig.NetworkMode;
    if (netns && netns.nsmode === "host") return 'netns "host" is not allowed';
    if (networkMode === "host") return 'NetworkMode "host" is not allowed';

    // 6. No namespace sharing with host
    if (spec.pid_ns   && spec.pid_ns.nsmode   === "host") return "host PID namespace sharing not allowed";
    if (spec.ipc_ns   && spec.ipc_ns.nsmode   === "host") return "host IPC namespace sharing not allowed";
    if (spec.uts_ns   && spec.uts_ns.nsmode   === "host") return "host UTS namespace sharing not allowed";
    if (spec.HostConfig) {
        if (spec.HostConfig.PidMode === "host")       return "host PID mode not allowed";
        if (spec.HostConfig.IpcMode === "host")       return "host IPC mode not allowed";
        if (spec.HostConfig.UTSMode === "host")       return "host UTS mode not allowed";
    }

    return null; // all checks passed
}

/**
 * Is this request a container create call?
 * Matches both Docker-compat and libpod paths.
 */
function isContainerCreate(method, urlPath) {
    if (method !== "POST") return false;
    // libpod: /v4.0.0/libpod/containers/create  (no trailing ID segment)
    if (/\/libpod\/containers\/create(\?.*)?$/.test(urlPath)) return true;
    // Docker-compat: /v1.xx/containers/create
    if (/\/containers\/create(\?.*)?$/.test(urlPath)) return true;
    return false;
}

// ── Proxy core ────────────────────────────────────────────────────────────────

function forwardRequest(clientReq, clientRes, bodyOverride) {
    const options = {
        socketPath: PODMAN_SOCKET,
        method: clientReq.method,
        path: clientReq.url,
        headers: { ...clientReq.headers },
    };

    // If we buffered the body (for inspection), fix Content-Length
    if (bodyOverride !== undefined) {
        options.headers["content-length"] = Buffer.byteLength(bodyOverride);
    }

    const proxyReq = http.request(options, (proxyRes) => {
        clientRes.writeHead(proxyRes.statusCode, proxyRes.headers);
        proxyRes.pipe(clientRes);
    });

    proxyReq.on("error", (err) => {
        log("error", `Upstream error: ${err.message}`);
        if (!clientRes.headersSent) {
            clientRes.writeHead(502);
        }
        clientRes.end(`Upstream error: ${err.message}`);
    });

    if (bodyOverride !== undefined) {
        proxyReq.write(bodyOverride);
        proxyReq.end();
    } else {
        clientReq.pipe(proxyReq);
    }
}

function deny(clientRes, reason, statusCode = 403) {
    log("warn", `DENIED: ${reason}`);
    const body = JSON.stringify({ message: `Blocked by VISP socket proxy: ${reason}` });
    clientRes.writeHead(statusCode, {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
    });
    clientRes.end(body);
}

const server = http.createServer((req, res) => {
    if (isContainerCreate(req.method, req.url)) {
        // Buffer the full body so we can inspect it before forwarding
        const chunks = [];
        req.on("data", (chunk) => chunks.push(chunk));
        req.on("end", () => {
            const body = Buffer.concat(chunks).toString("utf8");
            log("debug", `Container create request`, { url: req.url, bodyLen: body.length });

            const rejection = checkCreateBody(body);
            if (rejection) {
                log("warn", `Container create BLOCKED`, { reason: rejection, url: req.url });
                deny(res, rejection);
            } else {
                log("info", `Container create allowed`, { url: req.url });
                forwardRequest(req, res, body);
            }
        });
        req.on("error", (err) => {
            log("error", `Request read error: ${err.message}`);
            deny(res, "Request read error", 500);
        });
    } else {
        // All other requests pass through without inspection
        log("debug", `Pass-through: ${req.method} ${req.url}`);
        forwardRequest(req, res);
    }
});

// ── Startup ───────────────────────────────────────────────────────────────────

// Clean up stale socket file
if (fs.existsSync(PROXY_SOCKET)) {
    fs.unlinkSync(PROXY_SOCKET);
}

// Ensure the directory exists
const sockDir = path.dirname(PROXY_SOCKET);
if (!fs.existsSync(sockDir)) {
    fs.mkdirSync(sockDir, { recursive: true });
}

server.listen(PROXY_SOCKET, () => {
    // Make socket world-writable so session-manager (different UID inside container) can connect
    fs.chmodSync(PROXY_SOCKET, 0o666);
    log("info", `VISP Podman socket proxy listening`, {
        proxy: PROXY_SOCKET,
        upstream: PODMAN_SOCKET,
        absRootPath: ABS_ROOT_PATH,
    });
});

server.on("error", (err) => {
    log("error", `Server error: ${err.message}`);
    process.exit(1);
});

process.on("SIGTERM", () => {
    log("info", "Received SIGTERM, shutting down");
    server.close(() => {
        if (fs.existsSync(PROXY_SOCKET)) fs.unlinkSync(PROXY_SOCKET);
        process.exit(0);
    });
});

process.on("SIGINT", () => {
    log("info", "Received SIGINT, shutting down");
    server.close(() => {
        if (fs.existsSync(PROXY_SOCKET)) fs.unlinkSync(PROXY_SOCKET);
        process.exit(0);
    });
});

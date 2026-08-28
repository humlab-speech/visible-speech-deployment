#!/usr/bin/env node
/**
 * VISP Podman Socket Proxy
 *
 * Sits between session-manager and the real Podman socket. Only endpoints on
 * the allowlist below are forwarded; everything else is rejected.
 *
 * Endpoint allowlist (audited against external/session-manager/src, 2026-08-28):
 *   - GET  /containers/json            (container.list — refreshSessions, readiness checks)
 *   - GET  /images/json                (image.list — suspended-session import)
 *   - POST /containers/create          (inspected — see policy below)
 *   - POST /libpod/containers/create   (inspected — see policy below)
 *   - POST /containers/{id}/commit     (exportToImage)
 *   - POST /containers/{id}/exec       (runCommand — exec create; response is tracked)
 *   - POST /exec/{id}/start            (exec start — only for exec IDs created here)
 *   - GET  /containers/{id}/logs       (log streaming)
 *   - POST /containers/{id}/stop       (session delete)
 *   - POST /libpod/containers/{id}/start
 *   - POST /libpod/containers/{id}/stop (proxy sidecar cleanup)
 *
 * Container-targeted endpoints (commit/exec/logs/stop/start) are additionally
 * scoped: the {id} must resolve (via a cached /containers/json lookup) to a
 * container whose name starts with "visp-session-" — the prefix used by
 * Session.class.js getContainerName() for session containers and their
 * "-proxy" sidecars. Exec IDs are only accepted if they were created through
 * this proxy's scoped exec-create endpoint.
 *
 * Policy enforced on container create:
 *   1. image must match /^(localhost\/visp-|docker\.io\/library\/alpine)/
 *   2. container name must start with "visp-session-"
 *   3. privileged must be absent or false
 *   4. Every mount source must be under ABS_ROOT_PATH, must not be the root
 *      itself, and must not be under .env*, certs/ or mounts/mongo
 *   5. Mounts are forced read-only unless their source is under
 *      mounts/repositories/ (project data) or mounts/sessions/ (UDS socket dirs)
 *   6. cap_add may only contain caps from CAP_ALLOWLIST
 *   7. netns.nsmode must be "none" or "bridge" (no host networking)
 *   8. No host_pid / host_ipc / host_uts
 *
 * Usage:
 *   PODMAN_SOCKET=/run/user/1000/podman/podman.sock \
 *   PROXY_SOCKET=/run/podman-proxy/podman.sock \
 *   ABS_ROOT_PATH=/home/tomas/Projects/visible-speech-deployment \
 *   node proxy.js
 */

"use strict";

const http = require("http");
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

// Prefix of all container names session-manager may create or act on
// (Session.class.js getContainerName(): "visp-session-<projectId>-<userId>-<salt>",
// plus the "<name>-proxy" sidecar which keeps the same prefix).
const SESSION_CONTAINER_PREFIX = "visp-session-";

// Relative subtrees of ABS_ROOT_PATH that may be mounted read-write.
// Everything else under the root is forced read-only on create.
//   - mounts/repositories/<projectId>  project data (EMU-DB, audio, ...)
//   - mounts/sessions/<containerName>  UDS socket dir (socat/tinyproxy create
//     socket files there at runtime, so it must stay writable)
const RW_ALLOWED_SUBTREES = ["mounts/repositories", "mounts/sessions"];

// Relative subtrees of ABS_ROOT_PATH that must never be mounted.
const FORBIDDEN_SUBTREES = ["certs", "mounts/mongo"];

// ── Endpoint allowlist ────────────────────────────────────────────────────────
//
// Each entry: method, path pattern (anchored), and an inspect kind:
//   "create"     — buffer + validate the create body (checkCreateBody)
//   "container"  — the {id} segment must resolve to a session container name
//   "exec-create"— like "container", plus the exec ID from the response is
//                  tracked so /exec/{id}/start can be scoped
//   "exec-start" — the {id} segment must be a tracked exec ID
//   null         — read-only pass-through
//
// The optional "/vX.Y.Z/" version prefix is accepted (libpod calls use
// /v4.0.0/libpod/...; node-docker-api sends no version prefix).
const ENDPOINT_ALLOWLIST = [
    { method: "GET",  pattern: /^\/(?:v[\d.]+\/)?containers\/json(\?.*)?$/,            inspect: null },
    { method: "GET",  pattern: /^\/(?:v[\d.]+\/)?images\/json(\?.*)?$/,                 inspect: null },
    { method: "POST", pattern: /^\/(?:v[\d.]+\/)?containers\/create(\?.*)?$/,           inspect: "create" },
    { method: "POST", pattern: /^\/(?:v[\d.]+\/)?libpod\/containers\/create(\?.*)?$/,   inspect: "create" },
    { method: "POST", pattern: /^\/(?:v[\d.]+\/)?containers\/([^/?]+)\/commit(\?.*)?$/, inspect: "container" },
    { method: "POST", pattern: /^\/(?:v[\d.]+\/)?containers\/([^/?]+)\/exec(\?.*)?$/,   inspect: "exec-create" },
    { method: "POST", pattern: /^\/(?:v[\d.]+\/)?exec\/([^/?]+)\/start(\?.*)?$/,        inspect: "exec-start" },
    { method: "GET",  pattern: /^\/(?:v[\d.]+\/)?containers\/([^/?]+)\/logs(\?.*)?$/,   inspect: "container" },
    { method: "POST", pattern: /^\/(?:v[\d.]+\/)?containers\/([^/?]+)\/stop(\?.*)?$/,   inspect: "container" },
    { method: "POST", pattern: /^\/(?:v[\d.]+\/)?libpod\/containers\/([^/?]+)\/start(\?.*)?$/, inspect: "container" },
    { method: "POST", pattern: /^\/(?:v[\d.]+\/)?libpod\/containers\/([^/?]+)\/stop(\?.*)?$/,  inspect: "container" },
];

function matchAllowlist(method, urlPath) {
    for (const entry of ENDPOINT_ALLOWLIST) {
        if (entry.method !== method) continue;
        const m = urlPath.match(entry.pattern);
        if (m) return { inspect: entry.inspect, id: m[1] || null };
    }
    return null;
}

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

// ── Container ID → name resolution ────────────────────────────────────────────
//
// Container-targeted API paths carry container IDs, not names. To scope them
// to session containers we keep a short-TTL cache of ID→name built from the
// upstream /containers/json list.

const NAME_CACHE_TTL_MS = 5000;
const nameCache = { map: new Map(), fetchedAt: 0, inflight: null };

function refreshNameCache() {
    if (nameCache.inflight) return nameCache.inflight;
    nameCache.inflight = new Promise((resolve) => {
        const upstream = http.request(
            { socketPath: PODMAN_SOCKET, method: "GET", path: "/containers/json?all=true" },
            (upRes) => {
                let data = "";
                upRes.on("data", (d) => (data += d));
                upRes.on("end", () => {
                    try {
                        const map = new Map();
                        for (const c of JSON.parse(data)) {
                            const names = (c.Names || []).map((n) => n.replace(/^\//, ""));
                            const name = names[0] || "";
                            const fullId = c.Id || "";
                            if (fullId) {
                                map.set(fullId, name);
                                map.set(fullId.substring(0, 12), name);
                            }
                            for (const n of names) map.set(n, n);
                        }
                        nameCache.map = map;
                        nameCache.fetchedAt = Date.now();
                        log("debug", `Container name cache refreshed`, { entries: map.size });
                    } catch (e) {
                        log("warn", `Container name cache refresh failed: ${e.message}`);
                    }
                    resolve();
                });
            },
        );
        upstream.on("error", (err) => {
            log("warn", `Container name cache refresh error: ${err.message}`);
            resolve();
        });
        upstream.end();
    }).finally(() => {
        nameCache.inflight = null;
    });
    return nameCache.inflight;
}

async function resolveContainerName(id) {
    if (Date.now() - nameCache.fetchedAt > NAME_CACHE_TTL_MS) {
        await refreshNameCache();
    }
    let name = nameCache.map.get(id) || null;
    if (!name) {
        // Cache may be stale (e.g. container created moments ago) — force one
        // refresh before failing closed.
        await refreshNameCache();
        name = nameCache.map.get(id) || null;
    }
    return name;
}

// ── Exec ID tracking ──────────────────────────────────────────────────────────
//
// POST /exec/{id}/start is scoped to exec IDs created through this proxy's
// scoped POST /containers/{id}/exec endpoint, so a valid exec ID can only
// ever refer to a session container.

const EXEC_ID_TTL_MS = 30 * 60 * 1000;
const knownExecIds = new Map(); // execId -> expiry (ms)

function trackExecId(execId) {
    knownExecIds.set(execId, Date.now() + EXEC_ID_TTL_MS);
}

function isKnownExecId(execId) {
    const expiry = knownExecIds.get(execId);
    if (expiry === undefined) return false;
    if (Date.now() > expiry) {
        knownExecIds.delete(execId);
        return false;
    }
    return true;
}

// ── Create-body policy ────────────────────────────────────────────────────────

/**
 * Return a human-readable reason why the mount source is forbidden, or null
 * if it is acceptable (i.e. under ABS_ROOT_PATH, not the root itself, and not
 * inside a sensitive subtree).
 */
function mountSourceViolation(src) {
    const root = path.resolve(ABS_ROOT_PATH);
    const resolved = path.resolve(src);
    if (resolved === root) {
        return `Mount source "${src}" is the deployment root itself`;
    }
    const rel = path.relative(root, resolved);
    if (rel === "" || rel.startsWith("..")) {
        return `Mount source "${src}" is outside ABS_ROOT_PATH ("${ABS_ROOT_PATH}")`;
    }
    const first = rel.split(path.sep)[0];
    if (first.startsWith(".env")) {
        return `Mount source "${src}" is under a sensitive .env* path`;
    }
    for (const subtree of FORBIDDEN_SUBTREES) {
        if (rel === subtree || rel.startsWith(subtree + path.sep)) {
            return `Mount source "${src}" is under forbidden subtree "${subtree}/"`;
        }
    }
    return null;
}

function isRwAllowedSource(src) {
    const root = path.resolve(ABS_ROOT_PATH);
    const rel = path.relative(root, path.resolve(src));
    if (rel === "" || rel.startsWith("..")) return false;
    return RW_ALLOWED_SUBTREES.some(
        (subtree) => rel === subtree || rel.startsWith(subtree + path.sep),
    );
}

/**
 * Force read-only on every mount whose source is not in an rw-allowed subtree.
 * Rewrites the spec in place (libpod options arrays, Docker-compat Mounts and
 * Binds).
 */
function forceReadOnlyMounts(spec) {
    const libpodMounts = Array.isArray(spec.mounts) ? spec.mounts : [];
    for (const m of libpodMounts) {
        const src = m.source || m.Source || "";
        if (!src || isRwAllowedSource(src)) continue;
        if (Array.isArray(m.options)) {
            if (m.options.includes("rw")) {
                m.options = m.options.map((o) => (o === "rw" ? "ro" : o));
            } else if (!m.options.includes("ro")) {
                m.options.push("ro");
            }
        } else {
            // No options at all — Podman would default to rw, so be explicit.
            m.options = ["ro"];
        }
    }
    if (spec.HostConfig) {
        const compatMounts = Array.isArray(spec.HostConfig.Mounts) ? spec.HostConfig.Mounts : [];
        for (const m of compatMounts) {
            const src = m.Source || m.source || "";
            if (!src || isRwAllowedSource(src)) continue;
            m.ReadOnly = true;
            m.RW = false;
            if (typeof m.Mode === "string" && m.Mode) {
                m.Mode = m.Mode.replace(/rw/g, "ro");
            } else if (!m.Mode) {
                m.Mode = "ro";
            }
        }
        if (Array.isArray(spec.HostConfig.Binds)) {
            spec.HostConfig.Binds = spec.HostConfig.Binds.map((bind) => {
                const parts = bind.split(":");
                const src = parts[0];
                if (!src || isRwAllowedSource(src)) return bind;
                if ((parts[2] || "rw") === "ro") return bind;
                parts[2] = "ro";
                return parts.join(":");
            });
        }
    }
}

/**
 * Validate a libpod or Docker-compat container create body.
 * Returns null if OK, or a human-readable rejection reason string.
 * On success the spec may be rewritten in place (read-only enforcement);
 * callers must re-serialise the returned spec.
 */
function checkCreateBody(body) {
    let spec;
    try {
        spec = JSON.parse(body);
    } catch (e) {
        return { rejection: `Invalid JSON body: ${e.message}` };
    }

    // 1. Image allowlist
    const image = spec.image || spec.Image || "";
    if (!image) return { rejection: "Missing image field" };
    if (!IMAGE_PATTERN.test(image)) {
        return { rejection: `Image "${image}" is not in the allowlist (must match ${IMAGE_PATTERN})` };
    }

    // 2. Container name must be a session container
    const name = spec.name || spec.Name || "";
    if (!name.startsWith(SESSION_CONTAINER_PREFIX)) {
        return {
            rejection: `Container name "${name}" must start with "${SESSION_CONTAINER_PREFIX}"`,
        };
    }

    // 3. No privileged
    const privileged =
        spec.privileged ||                          // libpod SpecGenerator
        (spec.HostConfig && spec.HostConfig.Privileged); // Docker-compat
    if (privileged) return { rejection: "Privileged containers are not allowed" };

    // 4. Mount sources: under root, not the root itself, not sensitive
    if (ABS_ROOT_PATH) {
        const mounts = spec.mounts ||               // libpod SpecGenerator
            (spec.HostConfig && spec.HostConfig.Mounts) || [];
        // Also check Docker-compat Binds (host:container strings)
        const binds = (spec.HostConfig && spec.HostConfig.Binds) || [];

        for (const m of mounts) {
            const src = m.source || m.Source || "";
            if (!src) continue;
            const violation = mountSourceViolation(src);
            if (violation) return { rejection: violation };
        }
        for (const bind of binds) {
            const src = bind.split(":")[0];
            if (!src) continue;
            const violation = mountSourceViolation(src);
            if (violation) return { rejection: violation };
        }

        // 5. Force read-only outside the rw-allowed subtrees
        forceReadOnlyMounts(spec);
    }

    // 6. cap_add allowlist
    const capAdd =
        spec.cap_add ||                             // libpod SpecGenerator
        (spec.HostConfig && spec.HostConfig.CapAdd) || [];
    for (const cap of capAdd) {
        const normalised = cap.replace(/^CAP_/, "").toUpperCase();
        if (!CAP_ALLOWLIST.has(normalised)) {
            return { rejection: `Capability "${cap}" is not in the allowlist` };
        }
    }

    // 7. Network mode: only "none" or named bridge network (no "host")
    const netns = spec.netns;           // libpod: { nsmode: "none"|"bridge" }
    const networkMode = spec.HostConfig && spec.HostConfig.NetworkMode;
    if (netns && netns.nsmode === "host") return { rejection: 'netns "host" is not allowed' };
    if (networkMode === "host") return { rejection: 'NetworkMode "host" is not allowed' };

    // 8. No namespace sharing with host
    if (spec.pid_ns   && spec.pid_ns.nsmode   === "host") return { rejection: "host PID namespace sharing not allowed" };
    if (spec.ipc_ns   && spec.ipc_ns.nsmode   === "host") return { rejection: "host IPC namespace sharing not allowed" };
    if (spec.uts_ns   && spec.uts_ns.nsmode   === "host") return { rejection: "host UTS namespace sharing not allowed" };
    if (spec.HostConfig) {
        if (spec.HostConfig.PidMode === "host")       return { rejection: "host PID mode not allowed" };
        if (spec.HostConfig.IpcMode === "host")       return { rejection: "host IPC mode not allowed" };
        if (spec.HostConfig.UTSMode === "host")       return { rejection: "host UTS mode not allowed" };
    }

    return { rejection: null, spec }; // all checks passed
}

// ── Proxy core ────────────────────────────────────────────────────────────────

function forwardRequest(clientReq, clientRes, bodyOverride) {
    const options = {
        socketPath: PODMAN_SOCKET,
        method: clientReq.method,
        path: clientReq.url,
        headers: { ...clientReq.headers },
    };

    // If we buffered the body (for inspection), fix Content-Length. Drop
    // transfer-encoding — the buffered body is sent with an explicit length.
    if (bodyOverride !== undefined) {
        options.headers["content-length"] = Buffer.byteLength(bodyOverride);
        delete options.headers["transfer-encoding"];
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

/**
 * Forward an exec-create request and track the exec ID from the (small JSON)
 * response so that /exec/{id}/start can be scoped to session containers.
 */
function forwardAndTrackExecId(clientReq, clientRes) {
    const options = {
        socketPath: PODMAN_SOCKET,
        method: clientReq.method,
        path: clientReq.url,
        headers: { ...clientReq.headers },
    };

    const proxyReq = http.request(options, (proxyRes) => {
        const chunks = [];
        proxyRes.on("data", (c) => chunks.push(c));
        proxyRes.on("end", () => {
            const body = Buffer.concat(chunks);
            try {
                const parsed = JSON.parse(body.toString("utf8"));
                const execId = parsed.Id || parsed.ExecID;
                if (execId) {
                    trackExecId(execId);
                    log("debug", `Tracked exec ID`, { execId });
                }
            } catch (e) {
                // Non-JSON response (e.g. error page) — nothing to track.
            }
            clientRes.writeHead(proxyRes.statusCode, proxyRes.headers);
            clientRes.end(body);
        });
    });

    proxyReq.on("error", (err) => {
        log("error", `Upstream error: ${err.message}`);
        if (!clientRes.headersSent) {
            clientRes.writeHead(502);
        }
        clientRes.end(`Upstream error: ${err.message}`);
    });

    clientReq.pipe(proxyReq);
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

const server = http.createServer(async (req, res) => {
    const match = matchAllowlist(req.method, req.url);

    if (!match) {
        deny(res, `Endpoint not allowed: ${req.method} ${req.url}`);
        return;
    }

    try {
        switch (match.inspect) {
            case "create": {
                // Buffer the full body so we can inspect it before forwarding
                const chunks = [];
                req.on("data", (chunk) => chunks.push(chunk));
                req.on("end", () => {
                    const body = Buffer.concat(chunks).toString("utf8");
                    log("debug", `Container create request`, { url: req.url, bodyLen: body.length });

                    const { rejection, spec } = checkCreateBody(body);
                    if (rejection) {
                        log("warn", `Container create BLOCKED`, { reason: rejection, url: req.url });
                        deny(res, rejection);
                    } else {
                        log("info", `Container create allowed`, { url: req.url });
                        forwardRequest(req, res, JSON.stringify(spec));
                    }
                });
                req.on("error", (err) => {
                    log("error", `Request read error: ${err.message}`);
                    deny(res, "Request read error", 500);
                });
                return;
            }

            case "container":
            case "exec-create": {
                const name = await resolveContainerName(match.id);
                if (!name) {
                    deny(res, `Container "${match.id}" could not be resolved to a known container`);
                    return;
                }
                if (!name.startsWith(SESSION_CONTAINER_PREFIX)) {
                    deny(res, `Container "${name}" is not a session container (name must start with "${SESSION_CONTAINER_PREFIX}")`);
                    return;
                }
                log("debug", `Container-targeted request allowed`, { url: req.url, container: name });
                if (match.inspect === "exec-create") {
                    forwardAndTrackExecId(req, res);
                } else {
                    forwardRequest(req, res);
                }
                return;
            }

            case "exec-start": {
                if (!isKnownExecId(match.id)) {
                    deny(res, `Exec ID "${match.id}" was not created through this proxy`);
                    return;
                }
                log("debug", `Exec start allowed`, { url: req.url });
                forwardRequest(req, res);
                return;
            }

            default: {
                log("debug", `Pass-through: ${req.method} ${req.url}`);
                forwardRequest(req, res);
                return;
            }
        }
    } catch (err) {
        log("error", `Request handling error: ${err.message}`);
        if (!res.headersSent) {
            deny(res, `Internal proxy error: ${err.message}`, 500);
        }
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

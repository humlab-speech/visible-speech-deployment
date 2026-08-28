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
 *   - POST /containers/{id}/commit     (exportToImage — modern form)
 *   - POST /commit                     (exportToImage — legacy form used by
 *                                      node-docker-api: /commit?container=<id>)
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
 *   7. netns must be present with nsmode "none" or "bridge"; pid/ipc/uts
 *      namespaces, if present, must be "private" (no host or container
 *      namespace sharing in any form)
 *   8. Docker-compat PidMode/IpcMode/UTSMode/NetworkMode: only "", "private"
 *      or "none"
 *   9. no_new_privileges must be true and userns.nsmode must be "keep-id"
 *      (container root must never map onto the host user)
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
//   "commit"     — the container= query param must resolve to a session
//                  container name (legacy /commit endpoint)
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
    { method: "POST", pattern: /^\/(?:v[\d.]+\/)?commit(\?.*)?$/,                 inspect: "commit" },
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

// Realpath of ABS_ROOT_PATH, computed at startup (see Startup section).
// All mount-source checks compare against this, so a symlinked deployment
// root cannot defeat the relative-path arithmetic.
let ROOT_REAL = null;

/**
 * Resolve a mount source to its real (symlink-dereferenced) host path.
 * Returns { ok: true, real } or { ok: false, reason }.
 *
 * The proxy runs on the host, so host paths resolve here even when the
 * source is not mounted into session-manager — a symlink planted under a
 * writable subtree (e.g. mounts/repositories/) pointing at mounts/mongo/
 * or .env* is caught because the REAL target is what gets checked.
 */
function resolveMountSource(src) {
    let real;
    try {
        real = fs.realpathSync(path.resolve(src));
    } catch (e) {
        if (e.code === "ENOENT") {
            return { ok: false, reason: `Mount source "${src}" does not exist` };
        }
        return { ok: false, reason: `Mount source "${src}" could not be resolved: ${e.message}` };
    }
    return { ok: true, real };
}

/**
 * Return a human-readable reason why the mount source is forbidden, or null
 * if it is acceptable (i.e. its real path is under ABS_ROOT_PATH, not the
 * root itself, and not inside a sensitive subtree).
 */
function mountSourceViolation(src) {
    const r = resolveMountSource(src);
    if (!r.ok) return r.reason;
    const resolved = r.real;
    if (resolved === ROOT_REAL) {
        return `Mount source "${src}" is the deployment root itself`;
    }
    const rel = path.relative(ROOT_REAL, resolved);
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
    const r = resolveMountSource(src);
    if (!r.ok) return false;
    const rel = path.relative(ROOT_REAL, r.real);
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

    // 4. Mount sources: under root, not the root itself, not sensitive.
    //    (ROOT_REAL is guaranteed non-null — the proxy refuses to start
    //    without a resolvable ABS_ROOT_PATH.)
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

    // 7. Network namespace: strict allowlist. Session.class.js sends exactly
    //    { nsmode: "none" } or { nsmode: "bridge" } — everything else
    //    ("host", "container:<id>", "ns:/path", "slirp4netns", "pasta",
    //    a missing netns) is rejected.
    if (!spec.netns || (spec.netns.nsmode !== "none" && spec.netns.nsmode !== "bridge")) {
        return { rejection: 'netns.nsmode must be "none" or "bridge"' };
    }

    // 8. Other namespaces: no sharing with the host or with other
    //    containers. If present, nsmode must be "private".
    for (const [key, label] of [["pid_ns", "PID"], ["ipc_ns", "IPC"], ["uts_ns", "UTS"]]) {
        if (spec[key] && spec[key].nsmode !== "private") {
            return { rejection: `${label} namespace sharing not allowed (nsmode must be "private")` };
        }
    }

    // 9. Docker-compat namespace modes: only "", "private" or "none".
    if (spec.HostConfig) {
        for (const [key, label] of [
            ["NetworkMode", "NetworkMode"],
            ["PidMode", "PID mode"],
            ["IpcMode", "IPC mode"],
            ["UTSMode", "UTS mode"],
        ]) {
            const v = spec.HostConfig[key];
            if (v !== undefined && v !== "" && v !== "private" && v !== "none") {
                return { rejection: `${label} "${v}" is not allowed (only "", "private" or "none")` };
            }
        }
    }

    // 10. Rootless hardening: no_new_privileges and a keep-id user namespace
    //     are required, so container root can never map onto the host user.
    if (spec.no_new_privileges !== true) {
        return { rejection: "no_new_privileges must be true" };
    }
    if (!spec.userns || spec.userns.nsmode !== "keep-id") {
        return { rejection: 'userns.nsmode must be "keep-id"' };
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

/**
 * Resolve a container ID/name to a session container, denying the response
 * with a 403 if it cannot be resolved or is not a session container.
 * Returns the container name, or null (response already denied).
 */
async function assertSessionContainer(id, res) {
    const name = await resolveContainerName(id);
    if (!name) {
        deny(res, `Container "${id}" could not be resolved to a known container`);
        return null;
    }
    if (!name.startsWith(SESSION_CONTAINER_PREFIX)) {
        deny(res, `Container "${name}" is not a session container (name must start with "${SESSION_CONTAINER_PREFIX}")`);
        return null;
    }
    return name;
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
                const name = await assertSessionContainer(match.id, res);
                if (!name) return;
                log("debug", `Container-targeted request allowed`, { url: req.url, container: name });
                if (match.inspect === "exec-create") {
                    forwardAndTrackExecId(req, res);
                } else {
                    forwardRequest(req, res);
                }
                return;
            }

            case "commit": {
                // Legacy commit form (node-docker-api): POST /commit?container=<id>
                const containerId = new URL(req.url, "http://localhost").searchParams.get("container");
                if (!containerId) {
                    deny(res, "Missing required query parameter: container");
                    return;
                }
                const name = await assertSessionContainer(containerId, res);
                if (!name) return;
                log("debug", `Commit request allowed`, { url: req.url, container: name });
                forwardRequest(req, res);
                return;
            }

            case "exec-start": {
                if (!isKnownExecId(match.id)) {
                    deny(res, `Exec ID "${match.id}" was not created through this proxy (or the proxy restarted since the exec was created)`);
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

// Fail closed: without a resolvable ABS_ROOT_PATH, mount-source validation
// would be silently disabled. Refuse to start instead.
if (!ABS_ROOT_PATH) {
    log("error", "ABS_ROOT_PATH is not set — refusing to start (mount validation would be disabled)");
    process.exit(1);
}
try {
    ROOT_REAL = fs.realpathSync(path.resolve(ABS_ROOT_PATH));
} catch (e) {
    log("error", `ABS_ROOT_PATH "${ABS_ROOT_PATH}" cannot be resolved (${e.message}) — refusing to start`);
    process.exit(1);
}

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

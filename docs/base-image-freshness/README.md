# Base-image freshness for built images — findings & plan

Status: **implemented on this branch** (`feat/base-image-freshness`, Phases 1–2
below). The audit pins line numbers to `65345fe`; the branch was based on the
then-current master `465102d`, where all cited locations still hold — re-check
line drift before applying any of the quoted excerpts directly.

## The problem in one paragraph

`./visp.py build` defaults to podman's `--pull=missing`, so a rebuild never
re-resolves a base tag it already has locally — and official images are
rebuilt upstream **under the same tag** when patched. A freshly built
`visp-*` image can therefore bake a stale, vulnerability-bearing base while
every tool reports green. Built images carry no post-hoc record of which base
digest they consumed (`RepoDigests` is empty, layer-chain matching measured
unreliable: 1 of 20 diffIDs matched on a known-derived image), so build-time
recording keyed by built-image ID is the only exact source of truth.

PR #263 built every primitive needed to fix this (RegistryClient, digest
comparison, tag windows, offline-tolerant display) but wired them only to
quadlet `Image=` lines. FROM bases are out of scope there by construction.

## Contents

- `PROCEDURE.md` — inventory of every existing visp.py surface that touches
  image freshness and exactly what question each one does NOT answer.
- `PR-263-AUDIT.md` — code-level audit of the merged #263 (registry client
  mechanics, digest semantics, scope confirmation, full base-image inventory,
  10 residual risks).
- `DESIGN.md` — the recommended design: extend `./visp.py images` with a
  six-state per-base verdict (fresh / MOVED / heuristic / never-checked /
  offline(as-of) / immobile), base→dependents fan-out, `.visp/base-lock.json`
  keyed by built image ID, remediation phrased as build+apply, offline-first.

## Headline findings to not lose

1. `scan_base_images()` globs `Dockerfile*` — whisperx's base lives in a
   **Containerfile** (`external/WhisperVault/container/Containerfile`) and is
   invisible even to `images base`. Small fix, independent value.
2. `visp-podman-socket-proxy` (the session allowlist enforcer) runs on an
   unchecked `node:22.22.3-alpine3.22`.
3. `--pull=missing` default means today's printed remediation "just rebuild"
   does not actually refresh the base — the Phase 2 `--pull=newer` default is
   load-bearing for every MOVED hint to work.
4. Label stamping as provenance was evaluated and REJECTED: `--label` does not
   invalidate the layer cache and `--inherit-labels` defaults to true — a
   cache-hit build stamps fresh lies over old layers (same defect family as
   `build.timestamp` being written before `podman build` runs).
5. Follow-on hole named in the design: `build.py` runs
   `npm install --legacy-peer-deps`, not `npm ci` — exact base digests still
   won't make JS deps reproducible. Same PR family as the Dependabot-runtime
   bumps item.

## Phased implementation (from DESIGN.md §5)

- **Phase 1** (~320 lines incl. tests, zero build-behaviour change, works
  off-net): `vispctl/base_freshness.py` + `images` wiring + cached-check
  file; verdicts exact only when a lock record exists, honest `heuristic`
  otherwise.
- **Phase 2** (~45 lines in build.py + test): `--pull=newer` default
  (`--offline` → `--pull=never`), post-build base-lock write, base digest
  printed in build output.
- **Later, only on demand**: `--pin-base`, `images --json`,
  `images rebuild-stale`, committed bases.lock, CVE lookup, npm ci.

## Open questions for the implementer

- Cache/lock file location: `.visp/base-lock.json` vs the quadlet install
  dir — must be where `images` reads it regardless of cwd (the
  `versions.json` cwd-relative defect is the cautionary tale).
- Does `--pull=newer` change layer-cache behaviour enough to slow routine
  rebuilds noticeably? Measure once on visp-demo-class hardware.
- Multi-stage: runtime-vs-build classification from the target stage —
  confirm podman's target-stage graph is derivable without shelling into a
  build (design assumes Dockerfile parsing suffices).

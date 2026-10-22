# TASK THREE — Telling the user "this built image is not secure-fresh"

Design for **locally built `visp-*` images whose `FROM` base may be stale**.
Read-only audit; repo the deployment checkout, merged PR 263 = `65345fe`
(`vispctl/registry.py` + `vispctl/image_update.py` + third-party `images` section).
Where a file was changed by PR 263 the line numbers are from the **merged** file (fetched
from `?ref=65345fe`); `build.py`, `quadlets.py`, `versions.py` are identical to the working
tree (`diff` against `<deployment-checkout>//vispctl/build.py` reported no differences).

---

## 1. What the plumbing already gives us (evidence)

| Capability | Where | Notes |
|---|---|---|
| List built images (`visp-*`, tag, size, Created) | merged `vispctl/images.py:116` `get_visp_images` | shows **build timestamp only** — no freshness signal |
| Parse `FROM` bases of `docker/**` + `external/**` | merged `vispctl/images.py:291` `scan_base_images` | returns `base → [(Dockerfile, stage)]`, **all stages**, normalises `docker.io/library/` away. Called **only** by `display_base_images` (`images.py:524`) |
| Pinning classification of those bases | `vispctl/images.py:20` `_classify_tag` | `pinned`/`unpinned`/`digest`. Proven insufficient: `node:24.16.0-alpine3.22` classifies **pinned** yet its registry content moves |
| Registry client: parse ref, list tags, `HEAD` digest of a tag | `vispctl/registry.py:48` `ImageRef.parse`, `:93` `list_tags`, `:106` `tag_digest`; error type `:43` `RegistryError` | anonymous bearer tokens, 15 s timeout, HEAD (no anonymous pull-quota cost). This is the only network dependency we need |
| Parallel, failure-tolerant registry lookups | `vispctl/images.py:93` `_lookup_remote` + `:286` `ThreadPoolExecutor(max_workers=4)` | pattern: *record* the error string, never raise → the offline-friendly idiom to copy |
| Third-party digest comparison (same-tag-moved) | `vispctl/images.py:81` `ThirdPartyImage.digest_matches` (`None` = unknown), displayed at `images.py:435-448` | the exact 3-state shape (match / moved / unknown) this design reuses |
| `images update <service>` refuses built images | `vispctl/image_update.py:165` | `"'{service}' runs our own image — rebuild it with: ./visp.py build {service}"` → **pull is already not the remediation for built images** (requirement (c) is half-done) |
| Stale-**container** detection (running ≠ current tag) | merged `images.py:209` `get_stale_containers`, wired at `vispctl/quadlets.py:215,234,274,280` (`cmd_apply`) | ID compare only; it fires *after* a rebuild. It cannot know the base moved |
| Source-staleness rows (git labels) | `vispctl/deploy.py:289-320`, aggregated `deploy.py:768,815` | a *different* axis: image vs source/deploy commit. Complementary, see §6 |
| One image command | `visp.py:577` `cmd_images` → `display_visp_images` + `display_third_party_images(check_remote=not args.offline)` + `display_network_info`; `images base` at `visp.py:593`; `--offline` flag at `visp.py:1053` | `--offline` already exists and already means "skip the registry" |

**Dockerfile reality** (grep of the working tree) — this is the danger, in one table:

```
docker/artic/Dockerfile:10,20,34      FROM docker.io/library/node:22-bookworm-slim   (unpinned: moves every build)
docker/artic/Dockerfile:45            FROM docker.io/library/nginx:alpine            (unpinned)
docker/nginx/Dockerfile:1             FROM docker.io/library/nginx:alpine            (unpinned)
docker/hs-wsr-client/Dockerfile:1     FROM docker.io/library/httpd:2.4               (unpinned minor)
docker/session-proxy/Dockerfile:1     FROM docker.io/library/alpine:3.23             (unpinned)
docker/podman-socket-proxy/Dockerfile:1 FROM docker.io/node:22.22.3-alpine3.22       "pinned" — content still moves
docker/tratt/Dockerfile:2             FROM docker.io/node:24.15.0                    "pinned" — content still moves
docker/tratt/Dockerfile:32            FROM docker.io/httpd:2.4.67                    "pinned" — content still moves
external/wsrng-server/Dockerfile:7,20 FROM node:24.16.0-alpine3.22                   "pinned" — content still moves
external/session-manager/Dockerfile   FROM node:24.19.0-bookworm-slim ×2, debian:bookworm-20260505-slim
docker/apache/Dockerfile:6            FROM debian:trixie-20260406                    (snapshot tag: genuinely immobile)
docker/session-manager/vscode-session/Dockerfile:1 FROM linuxserver/code-server:version-v3.11.1
docker/session-manager/jupyter-session/Dockerfile:9 FROM quay.io/jupyter/datascience-notebook:r-4.5.2
```

Only the `debian:trixie-20260406` / `bookworm-20260505` **snapshot** tags are truly
content-immobile. Everything else — including the "pinned" dotted versions — can be
rebuilt upstream under the same tag. `_classify_tag` cannot express that; **digest
comparison is the only honest check.**

Fan-out (base → dependents), from `BUILD_CONFIGS` (`vispctl/build.py:577-645`) crossed
with the `FROM` grep above: one `node:24.x-alpine*` movement implicates
`wsrng-server` (+ anything built from `external/*` on node); `nginx:alpine` implicates
`artic`'s **runtime** stage *and* the standalone `docker/nginx` build; `httpd:2.4`
implicates `hs-wsr-client` and `webclient`'s `httpd:2.4.48`. Multi-stage matters:
for `artic`, `node:22-bookworm-slim` is a **build stage**, `nginx:alpine` is the
**runtime** stage (`docker/artic/Dockerfile:34` vs `:45`); the shipped-rootfs verdict must
be per-stage, `target: production` (`build.py:594`, `:606`).

## 2. Podman capabilities measured on this box (podman 5.7.0)

Read-only commands run here (nothing mutated):

1. `podman version` → **5.7.0** client and engine.
2. `podman build --help` → `--pull string[="always"]  Pull image policy
   ("always"|"missing"|"never"|"newer") (default "missing")`.
   **`--pull=newer` exists**: podman re-resolves the tag and pulls when the registry copy
   is newer than the local one. Also available: `--label`, `--layer-label`
   (intermediate images), `--annotation`, `--inherit-labels` (default **true**),
   `--identity-label` (default true), `--format`, `--no-cache`, `--cache-ttl`.
3. **The mechanism of the bug**: default policy is `missing`, so today's `./visp.py build`
   (which passes no `--pull` unless the flag is given — `build.py:253-254`) **never
   re-resolves a tag it already has locally**. A patched `node:24.16.0-alpine3.22` in the
   registry is ignored; the rebuild silently re-bakes the stale base. The only path that
   refreshes is the manual `--pull` (`visp.py:1018-1020`, help text "Always pull the
   latest base images", which passes bare `--pull` = `always`).
4. Built images carry **no** registry identity: `podman image inspect 3ac561db0466`
   (a locally built matomo-derived image on this box) → `RepoDigests=[]`, `Parent=` (empty),
   `Config.Labels=null`. `podman image inspect --format '{{.NamedReposDigests}}'` →
   **"can't evaluate field NamedReposDigests"** in 5.7. So podman gives us *nothing*
   post-hoc about what a built image was built `FROM`. Build-time recording is the only
   exact source. `RepoDigests` **is** populated for pulled images (used below).
5. **Layer-chain provenance does NOT work here** (this kills the cleverest no-build-change
   idea). Tested against the locally built image `3ac561db0466` (20 layers) whose `podman
   history` is byte-for-byte matomo's (`FROM php/matomo` lineage): comparing
   `.RootFS.Layers` diffID prefixes gave **1 of 20** layers matching
   `docker.io/library/matomo:5.14.0-apache` and 1 of 15 matching `php:8.4-apache` —
   podman's default `--format=oci` conversion/re-diff recomputes diffIDs, so a built
   image's layer list is **not** a prefix of its pulled base's list. Any design that
   proves "which base content is baked in" by layer matching would report garbage.
6. **The digest comparison itself works**: pulled `docker.io/library/node:24.16.0-alpine3.22`
   → `RepoDigests=[…@sha256:191c9f00…, …@sha256:8106d433…]`; merged `RegistryClient`
   (run from a copy in /tmp, no repo touched) reports the registry's current digest for
   that tag = `sha256:191c9f0080fc…` → **up-to-date path proven**, and
   `newer_versions()` returned `('24.22.1-alpine3.24', '26.12.1-alpine3.24')` for the same
   ref. Note the two digests: RepoDigests holds index **and** manifest digests, which is
   why `digest_matches` is a set-membership test (`images.py:88`) — reuse it as-is.
7. `podman image inspect --format '{{.Created}}'` on the base = when the **base itself**
   was built upstream (`node:24.16.0-alpine3.22` → `2026-05-21`), a useful display field,
   not a verdict.
8. `scan_base_images` gives Dockerfiles, not services; mapping is `BUILD_CONFIGS[svc]`
   `context` + `dockerfile` + `target` (`build.py:579-645`) — 11 built services, all
   resolvable, and `_lookup_remote`'s threadpool gives the fetch pattern.

## 3. Recommended minimal design

**Answer to (e): extend `./visp.py images`. No new verb.** It already (a) is the one
image question operators ask, (b) already has `--offline` (`visp.py:1053`), (c) already
does parallel registry work, and (d) already prints both built and third-party sections
(`visp.py:581-582`). A `freshness` verb would be a second entry point to the same data.
`images base` (`visp.py:593`) stays, and becomes the base→dependents view of the same
result rather than a separate parse (`scan_base_images` keeps its pinning role).

### 3.1 Detection

Per built service, from `BUILD_CONFIGS` → its Dockerfile → **stage graph from the build
`target`** (or the last stage) → the registry `FROM` refs actually reachable from the
target stage, each tagged `runtime` (its own base) or `build` (an earlier stage that
feeds the target's artifacts). For each ref, resolve *recorded* vs *registry*:

```
recorded  = cache/image-lock[built-image-id].bases[ref]      # exact, post-build, §3.4
local     = podman image inspect <ref> --format {{json .RepoDigests}}   # pulled base here
remote    = RegistryClient.tag_digest(ref)                  # what the tag serves NOW
```

State machine (requirement (b) — three states plus honest degradation):

| state | condition | meaning |
|---|---|---|
| `fresh` | `remote == recorded` | tag content has not moved since we built |
| `moved` | `remote != recorded` | **NOT SECURE FRESH** — rebuild. Build recency is irrelevant by construction: this compares digests, never timestamps (requirement (a)) |
| `heuristic` | no record (image built before stamping existed / built by hand), `remote != local` | the base **you have** is older than the tag; the baked-in content could be older still. Labelled as a guess |
| `never-checked` | no record, base not present locally, no cached check | unknown; no error noise |
| `offline (as of DATE)` | `RegistryError`, cached check exists | last known verdict + evidence age, no error (requirement (d)) |
| `immobile` | snapshot tag w/o registry movement possible in practice (`debian:trixie-20260406`) — decided by `tag_digest` == recorded *and* `_classify_tag(tag)=="pinned"` with a dated suffix | shown green, no recheck spam |

Refused cheap alternative: `podman pull --dry-run`, `podman image exists`+timestamps —
timestamps are exactly the lie being fixed.

### 3.2 Output shape (built section of `images`)

```
=== VISP Container Images ===

  ✓ wsrng-server   visp-wsrng-server:latest
      Size: 168 MB   Created: 3 hours ago
      Base:  node:24.16.0-alpine3.22  sha256:191c9f00  (build stage)  ✓ fresh
      Base:  node:24.16.0-alpine3.22  sha256:191c9f00  (runtime)      ✓ fresh

  ⚠ artic          visp-artic:latest
      Size: 212 MB   Created: yesterday          ← build recency proves nothing
      Base:  nginx:alpine   runtime   MOVED  built on sha256:7a683c65, tag now sha256:2abea424 (5 days ago)
             → ./visp.py build artic && ./visp.py apply artic
      Base:  node:22-bookworm-slim   build   moved too (same rebuild fixes it)

  ⚠ hs-wsr-client  visp-hs-wsr-client:latest
      Base:  httpd:2.4   runtime   MOVED …

  2 of 11 images are NOT FRESH: base content moved behind a tag since they were built.
  Rebuild (this pulls the refreshed base; a plain rebuild would not):
      ./visp.py build artic hs-wsr-client && ./visp.py apply
  Bases and who they implicate:
      nginx:alpine        → artic (runtime), nginx (runtime)
      httpd:2.4           → hs-wsr-client (runtime), webclient (runtime)
      node:22-bookworm-slim → artic (build stage only)
  Registry last checked: 2026-10-22 06:31 (fresh).
```

Rules: never print "last built N ago" as a green signal; always name the **remediation
= build**, never pull (requirement (c), consistent with `image_update.py:165`);
`build` alone is not enough until `--pull` default changes (§3.4/Phase 2), so the hint
says so; `apply` at the end because `get_stale_containers`
(`images.py:209` → `quadlets.py:234`) is what converts the new image into restarted
containers. Third-party section unchanged. `images base` gains the fan-out and the
runtime/build-stage distinction (its `└─ docker/...` lines already show file+stage).

### 3.3 Offline (requirement (d))

`./visp.py images --offline` (flag exists, `visp.py:1053`) → no network, verdicts come
from the cached check file with an age: `MOVED (checked 2026-10-20, 2 days ago)` or
`unknown — registry never checked on this machine`. `RegistryError` never aborts: the
merged code already models this (`_lookup_remote` stores `remote_error`,
`images.py:93-102`; `display_third_party_images` prints a yellow line and counts
failures, `images.py:411-414,472`). Copy that exactly. No new flag needed on `images`;
`build --offline` is Phase 2 (see below).

### 3.4 Exactness: what to record at build time (requirement (f))

Comparison is exact once we know **the digest the build actually consumed**, keyed by the
**built image ID** (content-addressed join key → the record can never describe a
different image than the one `podman images` lists).

Options compared:

1. **Label stamping** (`--label visp.base.digest.node=<sha>`, `build.py:269-286`
   already builds `--label` args). **Rejected as the source of truth.**
   `--label` is applied when the final image is committed and does **not** invalidate the
   layer cache, so a cache-hit build stamps fresh metadata over old layers — the *same*
   defect the prior audit found for `build.timestamp`, which is written at
   `build.py:331-332` (inside `_add_git_labels`) **before** `podman build` runs. It also
   *is* the false-reassurance machine this task exists to kill: a lying label reads as
   evidence. Additional hazard: `--inherit-labels` defaults to **true** (`podman build
   --help`), so a key can be present inherited from the base and look like a fresh stamp.
   Usable *only* as a human-readable convenience, never as the comparison input.
2. **`versions.json` lockfield.** Rejected. `versions.py:57` loads `"versions.json"`
   **cwd-relative** and merges defaults per component (`versions.py:96-102`); its content
   is git-SHA locks for `external/` components, rewritten by `deploy update` and read by
   `check_version_drift` (`build.py:135-190`, blocking in prod). Base digests have a
   different cadence, a different owner (registry, not our SCM) and a different key
   (built image ID, not component name); putting them there buys merge conflicts and
   cwd-sensitive reads. Its *committed-and-reviewable* property is worth having, but the
   same property is already covered by committing the Dockerfile/`Image=` change, which
   is what PR 263 does for third-party images (`image_update.py:257-259`).
3. **A lock/state file keyed by built image ID** — **chosen.**
   `.visp/base-lock.json` (gitignored; one line in `.gitignore`), written **after** a
   successful build:
   ```json
   {"sha256:9f3a…(built image id)": {
     "built_at": "2026-10-22T06:00:00Z",
     "pull_policy": "newer",
     "bases": {"node:24.16.0-alpine3.22": {"stage": "runtime", "digest": "sha256:191c9f00…",
                                           "id": "009664e33f0a", "remote_digest": "sha256:191c9f00…"}}}}
   ```
   Truthfulness argument: with `--pull=newer` (§3.5) podman re-resolves the tag *before*
   the build, nothing else moves that tag during a build, and the record is written after
   the build returns from `podman image inspect <ref> --format '{{json .RepoDigests}}'`.
   It records **content consumed**, not intent. If it is absent, we degrade to
   `heuristic`/`never-checked` — we never guess into "fresh".
   Optional strictness (`--pin-base`, Phase 3): `podman pull <ref>@<digest>` then
   `podman tag` it as the FROM tag before building, making "the recorded digest is what
   was baked" mechanically guaranteed even under `--pull=missing`/`never`, and making a
   moved digest bust the layer cache deterministically. Note: **do not** rewrite 15
   Dockerfiles to `FROM ${BASE}` + `--build-arg`; that's a large diff for the same
   guarantee.

### 3.5 Files, line counts, reuse

| File | Change | ~Lines |
|---|---|---|
| `vispctl/base_freshness.py` *(new)* | stage-graph `FROM` parse from `BUILD_CONFIGS[svc].{context,dockerfile,target}` (45); `BaseFreshness` dataclass + `state` property (30); fetch via **existing `RegistryClient.tag_digest`** + `ThreadPoolExecutor(max_workers=4)` and record-don't-raise, copied from `images.py:93,286` (40); lock/cache read/write keyed by image ID (25); `fan_out()` base→[(service, stage-role)] (20); `format_lines()` reusing `Colors`/`color` (25) | **~185** |
| `vispctl/images.py` | `ImageManager.get_base_freshness()` thin wrapper; two extra lines per built image in `display_visp_images` (`:367-398`) incl. remediation + summary + "last checked"; fan-out lines in `display_base_images` (`:524+`); share `_classify_tag` | **+80 / -12** |
| `visp.py` | pass `freshness` into `display_visp_images` (near `:581`); `images base` reuses it (`:593-597`); help-text updates only | **+8** |
| `vispctl/build.py` *(Phase 2)* | `--pull=newer` instead of bare `--pull` (`:253-254`) + `--offline`→`--pull=never`; post-build record of base digests keyed by built image ID | **+40** |
| `.gitignore` | `.visp/` | **+1** |
| `tests/vispctl/test_base_freshness.py` *(new)* | existing `_FakeRunner.run_quiet` idiom (`tests/vispctl/test_images.py:8-22`) + a fake `opener` for `RegistryClient(timeout=…, opener=…)` (`registry.py:83`) | **~140** |

No new dependency, no new network path, no new command name, one new module.

## 4. Alternatives considered — why not

- **New verb `images freshness` / `images stale`** — two commands to ask one question;
  the answer operators need belongs next to the image list they already read. Reconsider
  only when a machine consumer needs `--json`.
- **Fold into `./visp.py status`** — `status` must stay instant and off-net-safe; a
  per-base HEAD to docker.io (11+ repos) does not belong there. Instead: `status` reads
  **only** the cached check file and prints `images: 2 not fresh (checked 2026-10-22) → ./visp.py images`,
  zero network. (`status.py` is 71 lines; the cross-link costs ~6.)
- **Layer diffID prefix matching** for baked-in base — **measured broken on this box**
  (§2.5: 1/20 layers). Dead end with podman's OCI conversion.
- **`podman history` / `.Parent` / `NamedReposDigests`** as post-hoc provenance —
  history IDs are `<missing>` below the top entry, `Parent` empty, `NamedReposDigests`
  not a field in 5.7 (§2.4-2.5). Nothing to read post-build.
- **Compare build timestamp vs registry "last pushed"** — registries don't serve a
  reliable push time via the v2 API, and timestamp-vs-timestamp is the exact class of
  reasoning that produced the blind spot. Digest only.
- **Trust `scan_base_images` + `_classify_tag` pinning classification** — the existing
  green/red split (`images.py:20-35`, `:536-545`) marks `node:24.16.0-alpine3.22` "pinned ✓"
  while its content moves. Pinning answers "will the *tag* move?"; security needs "will the
  *content* move?". Keep them as separate columns, never as one verdict.
- **`--pull=always` as the default in build** — correct for freshness but rebuilds
  nothing-cached on every offline/demo build and breaks air-gapped builds. `newer` is the
  policy that is both cheap and honest, with `--offline` → `never`.
- **OSV/CVE or trivy/grype scanning** — the real answer to "is it vulnerable", but needs a
  vulnerability DB, is hostile to visp-demo's air gap, and changes the product. Out of
  scope; `--json` from Phase 3 hands off to it later.
- **Registry digest in an image label as the source of truth** — see §3.4.1: cache-hit
  lying, inherited-label leakage.
- **Committing a `bases.lock`** — reviewable, but it describes the *repo's intent*, not
  the content of the image sitting in the rootless container store. Cross-machine
  truth belongs in git; per-machine verdicts belong in the lock file. Chose per-machine
  lock; keep committed pins where they already are (Dockerfiles, quadlet `Image=`).

## 5. Phased path

**Today (1 PR, ~320 lines incl. tests, zero build changes, works off-net).**
`base_freshness.py` + `images` wiring + cache file + tests. Verdicts are `fresh` /
`moved` **only when a lock record exists**, so the first run reports
`heuristic — compares the base you have, not the one baked in` for existing images
(that honesty is the point), plus base→dependents fan-out, plus `MOVED`/`unknown`
offline from cache, plus the `status` one-liner off cache. Immediate value on visp-demo:
a `heuristic` row still catches "`node:24.16.0-alpine3.22` in your store is 5 majors and
1 security cycle behind the tag" (verified live: local digest == registry digest today,
`newer_versions` → `24.22.1-alpine3.24`, `26.12.1-alpine3.24`).

**This week (~45 lines in `build.py` + 1 test).**
(1) `build.py:253-254` passes `--pull=newer` by default (`--offline` → `--pull=never`,
bare `--pull`/`--no-cache` still force the stronger policies), so a rebuild *actually*
picks up the moved tag — today `missing` guarantees it does not (§2.3). Update
`visp.py:1018-1020` help text and `build.py:733-734`. (2) Post-build record of base
digests keyed by built image ID → `heuristic` disappears for rebuilt images, verdicts
become exact. (3) Build output prints the base digest it consumed, so the number is
visible where it is decided. (4) `images` summary line counts exact vs heuristic.

**Later (only when a need shows itself).**
`--pin-base` (pre-pull by digest + retag) for mechanical guarantees under `never`/offline
rebuilds; `images --json` for automation and for `status` to stop parsing; a
`images rebuild-stale` convenience that runs the printed `build … && apply …` once asked
for (reuse `cmd_build` + `quadlets.cmd_apply`, like `image_update.py` reuses them at
`:266-290`); committed `bases.lock` if base bumps ever need review; CVE lookup; and the
*next* freshness hole — `build.py:406` uses `npm install --legacy-peer-deps`, not
`npm ci`, so JS dependencies move under a fixed `package.json`, meaning an exact base
digest still does not make the rest of the image reproducible. `--pin-base` + `npm ci`
+ lockfiles is the follow-up conversation.

## 6. Non-goals / residual risks to state in the UI

- This measures **tag content movement**, not **vulnerability**. `fresh` = "the base is
  what the registry serves for that tag today", not "no CVEs". Say so in the summary line.
- Two freshness axes stay separate and both must be green to be safe: **base content**
  (this design) and **source/deploy commit** (`deploy.py:289-320` ⚠ STALE rows from the
  `git.commit*` labels). Do not merge the verdicts; cross-reference them.
- A container started before the rebuild is still running the old content
  (`images.py:209`, wired at `quadlets.py:234`) — hence `&& ./visp.py apply <svc>` in
  every remediation line.
- Registries can serve a *newer* digest for a tag that was pulled *after* the build for
  unrelated reasons (a manual `podman pull` of the tag moves the local `RepoDigests`
  under `heuristic`); the exact lock record removes that ambiguity, another reason
  Phase 2 finishes the design.
- `heuristic` on an off-net machine with an absent base and no cache says
  `never-checked`, not `fresh` — silence is never allowed to read as safe.

VERDICT=DESIGN_DONE

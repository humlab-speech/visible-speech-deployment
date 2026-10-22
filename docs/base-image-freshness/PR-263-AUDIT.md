# TASK TWO — Code-level audit of merged PR 263

Repo: `humlab-speech/visible-speech-deployment`.
Evidence base: commit patches `a9467d0`, `657ae17` (GitHub commits API, `Accept: …v3.patch`), merged-file
contents at the merge commit `65345fe` (`contents/PATH?ref=65345fe`, base64-decoded), and the merge-commit
metadata (`parents: [fb1e404, 657ae17]`; `pulls/263/commits` = exactly `a9467d0`, `657ae17`).

All line numbers below are **merged-file line numbers at `65345fe`** (files: `vispctl/registry.py` 240 L,
`vispctl/images.py` 576 L, `vispctl/image_update.py` 291 L, `visp.py` 1289 L, `vispctl/deploy.py` 1211 L).
For files PR 263 did not touch (`vispctl/build.py`, `quadlets/**`, `docker/**`), the identical working-tree
content at `fb1e404` (the merge's first parent) was read locally read-only.

PR 263 touches exactly 7 files (`a9467d0`: AGENTS.md, tests/vispctl/test_images.py, tests/vispctl/test_registry.py,
visp.py, vispctl/deploy.py, vispctl/images.py, vispctl/registry.py; `657ae17`: AGENTS.md,
tests/vispctl/test_image_update.py, visp.py, vispctl/image_update.py, vispctl/images.py, vispctl/registry.py).
It touches **no** Dockerfile and **no** quadlet.

---

## 1. New capabilities and their UI surfaces

| Capability | Code | UI surface (what the operator sees) |
|---|---|---|
| Enumerate every non-`localhost/` image the quadlets run | `scan_third_party_images()` images.py:38-58 | shared by `images` and `deploy status` |
| Local state of those images (id, RepoDigests set, pull date, other local tags of the same repo, containers started from an older image of the tag) | `get_third_party_images()` images.py:242-289; `ThirdPartyImage` images.py:61-90 | `vispctl/images.py:411-449` block `=== Third-party Images (pulled, not built) ===`, incl. `Also local:` (421) and `⚠ container '<x>' still runs an older image of this tag` (442-448) |
| Registry digest comparison for the pinned tag ("rebuilt upstream") | `_lookup_remote()` images.py:93-102 → `RegistryClient.tag_digest` registry.py:106-114 | `Registry:  '<tag>' was rebuilt upstream (<digest>)` + `./visp.py images update <svc>   (pick the current tag)` images.py:430-437; summary line images.py:455-461 |
| Newer-tag discovery (same major / later major) | `newer_versions()` registry.py:218-229 | `Newer:  latest 5.x: 5.14.0   ·   new major: none` images.py:439-440, `_newer_line` images.py:472-481; summary images.py:462-463 |
| Registry failure shown, never fatal | images.py:101-102, 424-425, 464-465 | `Registry:  check failed — <err>` and `N registry check(s) failed.` |
| `--offline` escape hatch | visp.py:1053 (`p_images.add_argument("--offline" …)`), visp.py:582 | prints `Registry not checked (--offline).` images.py:469 |
| `deploy status` third-party rows now from the same scan (so the placeholder's nginx is covered, and services sharing one image each get a row) | deploy.py:429-452 (replaces the inline glob loop deleted in `a9467d0`) | `deploy status` table rows `{Service, Image, Status=Pulled/Not pulled}` — **pull presence only, no registry check** |
| `images update <service>` — pick a tag, pull, rewrite quadlets, converge | `run_image_update()` image_update.py:151-262; wired visp.py:586-590 (`cmd_images_update`) and visp.py:1059-1066 | new subcommand `./visp.py images update <svc> [--to TAG] [-y]`; choice menu `prompt_for_tag` image_update.py:104-123; warnings `_warnings` image_update.py:126-141; Matomo `core:update` hint `POST_UPDATE_HINTS` image_update.py:31-37 printed at 255-257; `git commit <quadlets…>` hint 259-261 |
| `image` alias of `images` | visp.py:1050-1052 (`aliases=["img", "image"]`, from `657ae17`) | `./visp.py image …` |
| Doc surface | AGENTS.md hunks in both patches (Day-to-day block + new `### Third-party (pulled) images` section) | operator/agent documentation only |

Not added: any registry or freshness check for **locally built** (`visp-*`) images. `display_visp_images()`
(images.py:367-398) still prints only `Size:` and `Created:` — a build timestamp, no base-image evidence.

---

## 2. Registry-check scope: which images get digest-compared

Entry point: `scan_third_party_images(project_dir, mode)` images.py:38-58 — this is the function the task
brief calls "map_quadlet_images"; **that name does not exist anywhere in the merged tree** (`grep -rn
map_quadlet_images` over merged sources and the working tree: no hits).

How the set is built (images.py:45-58):

1. `files = sorted((project_dir / "quadlets" / mode).glob("*.container"))` — **only `.container` files,
   and only for the current mode** (`mode` comes from `get_current_mode()`, images.py:246).
2. plus exactly one extra file: `placeholder_template(project_dir, mode)` =
   `quadlets/placeholder/<mode>.container` (placeholder.py:37-39), appended if it exists (images.py:46-48).
3. For each file, name = `"placeholder"` for the placeholder file else `qfile.stem`; every line literally
   starting `Image=` contributes `ref = line.split("=",1)[1].strip()` (images.py:53-55).
4. Refs starting with `localhost/` are dropped (images.py:56-57).
5. Result: `Dict[ref, List[quadlet-name]]`, so one image used by N quadlets is one entry (images.py:57).

Digest is fetched only when the quadlet ref is *not* itself `@sha256:`-pinned (images.py:99-100); the tag list
is fetched unconditionally (images.py:97). A ref whose image is not pulled still gets a registry check
(images.py:284-287 covers every entry) but `digest_matches` is `None` (images.py:86-90) and the row prints
`(not pulled)` (images.py:418).

Concrete scope at `65345fe` (from the merged tree's quadlets; PR 263 did not modify them):

- mode `prod`: `docker.io/library/mariadb:10.11.16` (matomo-db), `docker.io/library/matomo:5.8.0`,
  `docker.io/library/mongo:6.0.27`, `docker.io/library/nginx:1.30.0-alpine3.23` (placeholder)
- mode `dev`: the above **plus** `docker.io/cirrusid/simplesamlphp:v2.4.2` (local-idp),
  `docker.io/library/mongo-express:1.0.2-20-alpine3.19`
- everything else in the quadlets is `localhost/visp-*` and therefore out of scope by images.py:56.

Out of scope **by construction**:
- `.network` files (`quadlets/*/visp-net.network`, `tratt-net.network`) — never opened; they carry no `Image=`
  directive, so this costs nothing, but the scan is `.container`-only.
- the *other* mode: run in `prod`, the dev-only `simplesamlphp` and `mongo-express` pins are never checked.
- `docker-compose*.yml` (still in the repo root) — not parsed by anything added.
- `FROM` bases of every Dockerfile — see §7.
- build/pull-time-only images: `node:20-alpine` default (build.py:360), `node:22.22.2`
  (build.py:666), `docker.io/library/composer:2.9.5` (build.py:659; default build.py:445) — pulled by the Node/PHP build path,
  invisible to both `images` sections.

---

## 3. `RegistryClient` mechanics (registry.py:80-151)

- **Auth**: anonymous only. `_request` (registry.py:116-132) sends `Authorization: Bearer <tok>` if a token is
  cached for `(registry, repository)` (registry.py:117-121); on the first `401` it calls `_fetch_token` with the
  `WWW-Authenticate` challenge and retries once (registry.py:124-127). `_fetch_token` (registry.py:134-151)
  requires a `Bearer ` challenge, parses `key="value"` params, GETs `realm?params`, accepts `token` or
  `access_token`. No credentials are read from `containers/auth.json`/`podman login`, so private registries and
  Docker-Hub-authenticated pulls are not supported (docstring registry.py:13 states this deliberately).
- **Rate limits**: no 429/5xx special-casing, no backoff, no `Retry-After` handling. A rate-limited answer
  becomes an ordinary `RegistryError("… answered HTTP 429 …")` (registry.py:128) and is *displayed as a check
  failure*, indistinguishable from "no newer version". The only mitigation is the docstring claim
  "Uses HEAD for digests: on Docker Hub, HEAD requests do not count against the anonymous pull rate limit"
  (registry.py:84-85) — an unverified assertion in code, not enforced logic. Cost per image: 1 token GET +
  ≥1 `tags/list` GET + 1 `manifests` HEAD (Docker Hub tag lists are paginated, so long-lived repos such as
  node/nginx cost several GETs each). Concurrency is 4 threads sharing one client (images.py:284-287).
- **HEAD vs GET**: digests via `HEAD /v2/<repo>/manifests/<tag>` with `Accept` ordered
  OCI index → docker manifest list → OCI manifest → docker manifest v2 (registry.py:32-40, 106-114); tags via
  `GET /v2/<repo>/tags/list` following `Link: rel="next"` to an absolute URL (registry.py:93-104, `_next_link`
  registry.py:154-161). Timeout 15 s per request (registry.py:88).
- **Failure containment**: every network/HTTP/JSON problem is converted to `RegistryError`
  (registry.py:102, 113, 128, 131, 137, 141, 147, 150). In the `images` verb the error is *recorded per image*
  (`img.remote_error`, images.py:101-102) and the rest of the report still renders — the verb never fails.
  In `images update` the same error is fatal: `ServiceError("Registry check failed: …")`
  (image_update.py:178-182). Note `_lookup_remote` runs both calls in one `try`, so a tag-list failure also
  suppresses that image's digest comparison (images.py:96-102).
- **Digest semantics**: `Docker-Content-Digest` of the manifest *the Accept header negotiated*, i.e. for a
  multi-arch tag the **image-index / manifest-list digest**, not the platform config digest
  (module docstring registry.py:15-18; test pins the index media type in `Accept`, test_registry.py:105).
  The local side is the *set* of every `@sha256:…` tail of the image's `RepoDigests`
  (`digests = {d.partition("@")[2] for d in entry.get("RepoDigests") or []}`, images.py:267), and the
  comparison is membership (`self.remote_digest in self.local_digests`, images.py:90). Comparing against the
  whole RepoDigests set rather than one entry is what keeps registries that record both index and manifest
  digests from producing false "rebuilt upstream". Podman's image **config Id is never compared** — correct,
  since a base-image rebuild changes layers but the Id comparison would be tag-semantics, not registry truth.
  Caveat: an image pulled with an older/different media-type negotiation can carry only a manifest digest that
  the current Accept order no longer returns → false `digest_matches is False`; there is no fallback to
  `podman image inspect .Digest`/re-pull verification.
- `ImageRef.parse` (registry.py:56-77): handles `docker.io/`/`index.docker.io` → `registry-1.docker.io`,
  `library/` prefixing, registry ports (`localhost:5000/team/app:1.2` → host kept), `@sha256` refs, bare
  `nginx` → `library/nginx:latest`. Covered by test_registry.py:13-29.

---

## 4. `newer_version` tag logic (registry.py:164-240)

- `_VERSION_TAG = ^ (v?) (\d+(?:\.\d+)*) (.*) $` (registry.py:166). `_version_key` (registry.py:169-186)
  returns `(shape, numbers, variant)` where `shape = (prefix, len(numbers), digits-blank-suffix)` and
  `variant = tuple(ints in suffix)`.
  - **Same-shape gate** (registry.py:207): `5.8.0` vs `5.14` (moving tag, 2 components) and `5.14.0-fpm`
    (different suffix) never compare; `1.30.0-alpine3.23` vs `1.30.1-alpine3.24` do (suffix both
    `-alpine#.##`), with the variant tuple `(-)`→`(3,23)` vs `(3,24)` compared as part of `rank`
    (`rank = (key[1], key[2])`, registry.py:209) — so a base-image bump in the suffix counts as "newer"
    (test_registry.py:51-55).
  - **Pre-releases**: `8.0.0-rc1` has suffix `-rc1` vs `-` → different shape → never offered to a stable pin
    (test_registry.py:47-48). Converse: a pinned `-rc` only ever compares with `-rc`s.
  - **`v` prefix**: part of `shape`, so `v2.4.2` never matches `2.6.0` (test_registry.py:57-58).
  - Non-version tags (`latest`, `bookworm`, `stable-alpine`, `None`) → `is_version_tag` False
    (registry.py:189-190) and the UI prints `n/a — tag is not a version` (images.py:474-475).
- **Major windows**: `newest_by_major` (registry.py:193-215) maps every major to its newest strictly-newer
  same-shape tag (`rank <= (numbers, variant)` skip, registry.py:210-211). `newer_versions`
  (registry.py:218-229) reduces that to `(by_major.get(current_major), highest-later-major)`, so the `images`
  verb shows exactly two numbers and cannot see intermediate majors; `images update` uses `newest_by_major`
  directly so each later major is offered separately ("move a database one major at a time",
  image_update.py:96-105).
- `major_of` (registry.py:232-234) and `is_older` (registry.py:237-240, same-shape only) feed
  `_warnings`: downgrade → `… is OLDER than …` (image_update.py:128-131); different majors → major-upgrade
  text plus `This skips N major version(s)` (image_update.py:134-140).
- **The date-like 4-digit rule is *not* part of this module.** It lives in `_classify_tag`
  (images.py:21-35): `if "." in tag or re.search(r"\d{4,}", tag): return "pinned"`. It predates PR 263
  (introduced `8477db6` "fix(images): polish images/mode/backup output nits", 2026-08-20; it appears only as
  unchanged context in `a9467d0`, hunk header `@@ -28,6 +35,73 @@`), and it is used **only** by
  `display_base_images` (images.py:546, 563) for red/green colouring. Net effect after PR 263: the two
  classifiers disagree and never talk to each other — `_classify_tag` answers "can this tag move on paper?",
  `registry.py` answers "has this tag's content moved?", and the second question is only ever asked about
  quadlet-pulled images. `debian:trixie-20260406` is "pinned ✓" forever while drifting further behind, and
  `node:24.16.0-alpine3.22` is "pinned ✓" while the registry re-pushes content under it.

---

## 5. `images update` flow (image_update.py:151-291)

Order is fail-safe-first, as the module docstring states (image_update.py:1-12):

1. Guard rails: no `Image=` pin → `ConfigError` (162-164); any pin is `localhost/` → "runs our own image —
   rebuild it" (165-166); ref pinned by digest → refuse (171-172); different repository per mode → refuse
   (173-175).
2. Registry first: `list_tags` + `tag_digest`; failure is fatal here (177-182).
3. Local state via `ImageManager(cfg.runner).get_third_party_images(check_remote=False)` matched on
   `i.ref == current.ref` (184-189) → `pulled`, `rebuilt = pulled and remote_digest not in local.local_digests`.
4. Tag selection: `--to` must be in the registry tag list (204-207), otherwise numbered choices
   (`build_choices` 96-109: newest of current major, newest of each later major, plus the current tag when
   `rebuilt or not pulled`) or **any** tag the registry has (112-123, retried on unknown input); empty answer →
   `Cancelled.` rc 1; EOF (non-interactive) → `ConfigError("… Pass the tag with --to.")`.
   Minor wart: when `choices` is empty it prints "is the newest version and matches the registry" and then
   *still* prompts (210-212), so the only exit is a typed tag or cancel (rc 1).
5. Warnings then one confirmation: `_warnings` (221-223) and `_confirm` (230); **`-y/--yes` skips only the
   confirmation**, never the tag choice (230; visp.py:1065). Declining → `Cancelled.` rc 1, nothing pulled
   (verified by test_image_update.py:188-192).
6. **Pull before rewrite**: `podman pull <new_ref>` with `check=False`; nonzero rc raises
   `ServiceError("Pulling … failed. Nothing was changed.")` *before* any file is touched (234-237;
   test_image_update.py:181-186 asserts the pins are unchanged).
7. **Rewrite both modes**: `retagging = any(p.ref != retag(p.ref, new_tag))` (218); if true, every pin from
   `find_image_pins` is rewritten (240-244). `find_image_pins` (56-70) walks `MODES = ("dev","prod")`
   (image_update.py:26) over `quadlets/<mode>/<service>.container`, or the placeholder template for
   `service == "placeholder"` (61-65), collecting every `Image=` line. `retag` (73-79) drops any `@sha256` and
   the old tag; `set_image_pin` (82-88) is a `^Image=<old ref>[ \t]*$` single-substitution with `re.M` and
   raises if the line vanished — other directives (`Network=`, `ContainerName=`) are untouched
   (test_image_update.py:56-63).
8. **Convergence**: `retagging` → `_apply(service, cfg)` (249-250) which calls the real
   `vispctl.quadlets.cmd_apply` with the same args visp.py passes (265-282) — i.e. install-changed-quadlet +
   daemon-reload + restart, exactly `./visp.py apply <svc>`; a service disabled in `.env` degrades to a
   warning instead of failing (270-274). Same-tag re-pull → `_restart` = `ServiceManager.stop([svc])` then
   `start([svc])` over `get_runtime_services(include_disabled=True)` (285-291). `placeholder` never
   auto-applies: it prints "uses the new image the next time it is brought up (./visp.py placeholder up)"
   (247-248).
9. Post-steps: `POST_UPDATE_HINTS` (Matomo `podman exec matomo php /var/www/html/console core:update --yes`)
   only when retagging (31-37, 254-258); the `git commit …` line for the rewritten quadlets (259-261).
10. Note: `_apply` inherits `apply`'s stale-container warning path (`quadlets.py:215-241` calls
    `get_stale_containers`, `quadlets.py:234`), so a same-tag re-pull that skips apply relies on `_restart`
    instead — a stop/start, not a unit-redispatch.

---

## 6. Test surface added (what is actually pinned)

`tests/vispctl/test_registry.py` (new, 139 L):
- `test_image_ref_parse` (13-29, 8 cases): docker.io→registry-1, `library/` prefixing, quay.io, `localhost:5000`
  port, bare `nginx`→`latest`, `@sha256` with and without tag.
- `newer_versions`: numeric not lexical within a major and moving/variant tags ignored → `("5.14.0", None)`
  (37-39); later major reported separately (42-45); pre-releases skipped (47-48); variant numbers follow
  (`alpine3.23`→`alpine3.24`) (51-55); `v` prefix kept apart (57-58); current-is-newest → `(None, None)`
  (61-62); non-version tags `(None, None)` and `not is_version_tag` (65-69).
- Client (74-139): a fake opener that *requires* a bearer challenge with the right `service`/`scope`
  (94-95), follows `Link` pagination (99-102), **asserts the digest request is `HEAD` and asks for the OCI
  index media type** (104-105), asserts the token is fetched **once** across pages+digest (122) and that the
  last request is `HEAD` (123); `URLError` → `RegistryError("cannot reach …")` (126-131); HTTP 404 →
  `RegistryError("HTTP 404")` (134-139).

`tests/vispctl/test_images.py` (+130 L at `a9467d0`; merged 276 L):
- `test_scan_third_party_images_skips_localhost_and_includes_placeholder` (166-176): the exact dict
  `{ref: [quadlet]}`, `localhost/visp-apache` excluded, placeholder attributed to `"placeholder"`.
- `test_get_third_party_images_compares_digests_and_versions` (205-258): matomo same-tag-rebuilt →
  `digest_matches is False`; `other_local_tags == ["5.14.0"]`; `stale_containers == ["matomo"]` from the
  `podman ps` ImageID mismatch; `(latest_same_major, latest_new_major) == ("5.14.0", None)`; mongo
  `digest_matches is True`, `("6.0.28", "9.0.4")`; nginx not pulled → `local_id is None`,
  `digest_matches is None`, and the fake client's `RegistryError` surfaces verbatim as `remote_error`.
- `test_get_third_party_images_offline_skips_registry` (261-276): a client whose every attribute raises
  `AssertionError` proves `check_remote=False` touches no network; `remote_digest`/`remote_error` both None.
- Pre-existing stale/base-image tests retained unchanged: `test_get_stale_containers_*` (27, 39, 48),
  `test_get_container_networks_*` (63, 84), `test_classify_tag_*` (100, 110, 121),
  `test_scan_base_images_includes_stage_names` (130) — the last pins **Dockerfile text parsing only**
  (`base["node:24.15.0"] == [("docker/octra/Dockerfile", "builder")]`), no registry, no freshness.

`tests/vispctl/test_image_update.py` (new, 212 L):
- both-mode pin discovery (37-41); `retag` incl. digest-strip and `localhost:5000` (52-53);
  `set_image_pin` writes only the `Image=` line (56-63); choices list one entry per newer major and label
  majors with "major upgrade" (65-70); current tag offered only when rebuilt (72-77); prompt accepts number or
  any registry tag, retries, empty cancels, EOF → `--to` error (90-99); `_warnings` for minor/major/downgrade
  incl. "skips 2 major" (101-106).
- Flow: pull→rewrite both quadlets→apply, plus `core:update` and `git commit` output (168-179);
  **failed pull changes nothing** (181-186); declining confirmation pulls nothing and calls nothing
  (188-192); rebuilt-tag path pulls the same ref, leaves both quadlets untouched and calls `restart` not
  `apply` (195-201); `--to` must exist in the registry (204-206); `localhost/` images refused with "build"
  (209-212).

Nothing in the added test surface asserts anything about Dockerfile `FROM` bases vs the registry — there is no
test that could fail if that check were added or removed (see §7).

---

## 7. CONFIRMED: `FROM` bases of Dockerfiles are excluded from registry checks

Independent confirmation, three ways:

1. **Import graph.** `vispctl/registry.py` is imported by exactly two modules at `65345fe`:
   `vispctl/images.py:16` and `vispctl/image_update.py:26` (grep over merged sources). No other module
   references `RegistryClient`/`tag_digest`/`list_tags`.
2. **The base-image path performs no I/O.** `scan_base_images()` (images.py:291-365) only globs
   `docker/**/Dockerfile*` and `external/**/Dockerfile*` (images.py:303-304) and parses `^FROM` lines;
   `display_base_images()` (images.py:524-576) calls only `scan_base_images` + `_classify_tag` and prints
   colours/summary — no registry call anywhere. Its **only** caller is `display_base_images`
   (images.py:529), reached only from `cmd_images_base` (visp.py:593-597, the `images base` subcommand).
   Grep of `scan_base_images` across merged sources: 2 hits (definition + that call site).
3. **The third-party path never looks at Dockerfiles.** `scan_third_party_images` reads only
   `quadlets/<mode>/*.container` + the placeholder quadlet (images.py:45-58); it is the sole source of refs
   for `get_third_party_images` (images.py:246) and for `deploy status` (deploy.py:435-440). `display_visp_images`
   (built images, images.py:367-398) prints `Size`/`Created` only, from `podman images` (images.py:126-133).
   `image_update` explicitly refuses `localhost/` services (image_update.py:165-166) — i.e. the one verb that
   can consult the registry *declines* for every locally built image.

Corroborating mechanism (why the gap matters, unchanged by PR 263): `podman build` gets `--pull` only when the
operator passes it — `pull: bool = False` (build.py:242) → `if pull: cmd.append("--pull")` (build.py:253-254),
surfaced as `./visp.py build --pull` (build.py:685, 733-735). So a default rebuild re-resolves nothing: even a
*moving* `FROM` tag (`node:22-bookworm-slim`, `nginx:alpine`, `alpine:3.23`) is satisfied from the local store,
and a *patch-pinned* tag (`node:24.16.0-alpine3.22`, `httpd:2.4.67`, `python:3.12.11-slim`) is re-pushed under
the same tag by upstream rebuilds, so `podman build` (with or without `--pull`) sees a tag that has not moved.
Either way the resulting `visp-*` image carries a fresh `Created:` timestamp and stale layers, and nothing in
`images`, `images base` or `images update` can tell.

### Base images actually referenced, with Dockerfile path

Repo-owned (`docker/`, at `65345fe`; `pinned/unpinned` per `_classify_tag`, images.py:21-35 — which is a
*textual* judgement, **not** a freshness judgement):

| Base image | Dockerfile:line | Role | `_classify_tag` | Freshness risk (registry check absent) |
|---|---|---|---|---|
| `docker.io/library/debian:trixie-20260406` | `docker/apache/Dockerfile:6` (`AS base`, used by 75/101/123) | build + runtime base of `visp-apache` | pinned (date) | immutable snapshot → drifts forever; nobody tells you a newer snapshot exists |
| `docker.io/library/node:22-bookworm-slim` | `docker/artic/Dockerfile:10,20,34` | build stages of `visp-artic` | unpinned | moving tag, never re-pulled without `--pull` |
| `docker.io/library/nginx:alpine` | `docker/artic/Dockerfile:45` (`AS production`) | **runtime** base of `visp-artic` | unpinned | moving tag, rebuilt upstream silently |
| `docker.io/library/httpd:2.4` | `docker/hs-wsr-client/Dockerfile:1` | sole base | pinned (has ".") | **false confidence**: `2.4` is a moving minor tag |
| `docker.io/library/nginx:alpine` | `docker/nginx/Dockerfile:1` | sole base (no `nginx` key in `BUILD_CONFIGS`, build.py:578-640) | unpinned | legacy path, unchecked |
| `docker.io/node:22.22.3-alpine3.22` | `docker/podman-socket-proxy/Dockerfile:1` | sole base of the security-critical `visp-podman-socket-proxy` (allowlist enforcer) | pinned | **patch tag re-pushed under the same tag** — exactly the user's scenario, on the component that gates session containers |
| `docker.io/library/debian:bookworm-20260505` | `docker/session-manager/build-context/Dockerfile:5` | base (legacy context) | pinned (date) | drifts silently |
| `docker.io/library/node:22.22.2-bookworm` | `docker/session-manager/dev/Dockerfile:1` | dev session image | pinned | patch tag re-pushed |
| `docker.io/node:20.20.2-alpine3.22` | `docker/session-manager/jupyter-session/Dockerfile:2` (`AS container_agent_builder`) | build stage of `visp-jupyter-session` | pinned | patch tag re-pushed |
| `quay.io/jupyter/datascience-notebook:r-4.5.2` | `docker/session-manager/jupyter-session/Dockerfile:9` (`AS base`, → 36 `r_packages` → 59 `final`) | **runtime** base of `visp-jupyter-session` (also "used for operations tasks", build.py:620-627) | pinned | quay.io is reachable by the merged client (test_registry.py:19 pins `quay.io` parsing) yet is **never queried**, because it is a FROM, not a quadlet `Image=` |
| `linuxserver/code-server:version-v3.11.1` | `docker/session-manager/vscode-session/Dockerfile:1` | sole base of the vscode session image | pinned | third-party rebuild practice unknown; unchecked |
| `docker.io/library/alpine:3.23` | `docker/session-proxy/Dockerfile:1` | sole base of `visp-session-proxy` (tinyproxy sidecar) | unpinned | moving tag; `3.23` receives security rebuilds under the same tag |
| `docker.io/node:24.15.0` | `docker/tratt/Dockerfile:2` (`AS build`) | build stage of `visp-tratt` | pinned | patch tag re-pushed |
| `docker.io/httpd:2.4.67` | `docker/tratt/Dockerfile:32` | **runtime** base of `visp-tratt` | pinned | patch tag re-pushed |
| `docker.io/library/debian:bookworm-20260406-slim` | `docker/whisper/Dockerfile:4` | sole base (legacy: `whisperx` builds from `external/WhisperVault/container/Containerfile`, build.py:613-618) | pinned (date) | drifts silently |

External checkouts (`external/*` are separate repos, absent from the `65345fe` tree — no `external/` entries, no
`.gitmodules`, confirmed from `git/trees/65345fe?recursive=1`); `scan_base_images` globs them anyway
(images.py:303) and `AGENTS.md` documents them, but their content is whatever commit is checked out. Read on
this machine read-only:

| Base image | Dockerfile:line | Builds | Risk |
|---|---|---|---|
| `node:24.19.0-bookworm-slim` | `external/session-manager/Dockerfile:7,23` | `visp-session-manager` build stages | patch tag re-pushed |
| `debian:bookworm-20260505-slim` | `external/session-manager/Dockerfile:39` | **runtime** base of `visp-session-manager` | date snapshot, drifts |
| `node:24.16.0-alpine3.22` | `external/wsrng-server/Dockerfile:7,20` | build **and runtime** base of `visp-wsrng-server` | the task's example: `AGENTS.md` calls it "fully pinned ✅" while the digest under that tag moves |
| `node:24.15.0-bookworm-slim` | `external/emu-webapp-server/docker/Dockerfile:1` | sole base of `visp-emu-webapp-server` | patch tag re-pushed |
| `python:3.12.11-slim` | `external/WhisperVault/container/Containerfile:4` | sole base of `visp-whisperx` | **invisible even to `images base`**: the glob is `external/**/Dockerfile*` (images.py:303), which does not match `Containerfile` (`whisperx` build config: build.py:613-618) |
| `docker.io/library/httpd:2.4.48` | `external/webclient/Dockerfile:1` | legacy (apache builds via `docker/apache/Dockerfile`, build.py:579-587) | unchecked |
| `docker.io/library/debian:trixie-20260406` | `external/webclient/docker/Dockerfile:6` | mirror of `docker/apache/Dockerfile:6` | drifts silently |

Not covered by *anything* (neither `images` third-party section nor `images base`): `node:20-alpine`
(build.py:360 default `container_image`), `node:22.22.2` (build.py:666),
`docker.io/library/composer:2.9.5` (build.py:659, build.py:445) — all pulled, used for tool builds, and not `FROM`-parsed.

Note also that `docker/artic/Dockerfile`, `docker/tratt/Dockerfile`, `docker/podman-socket-proxy/Dockerfile`
etc. reference bases **without** the registry host or with `docker.io/` prefixes, so any future check must
normalise via `ImageRef.parse` (registry.py:56-77) — the machinery exists and is tested, it is simply not
wired to `scan_base_images`.

---

## Residual risks / gaps visible from this audit (not fixed by PR 263)

1. **Locally built images have no freshness signal at all** (§7) — the central concern stands.
2. `--pull` is opt-in (build.py:242, 253-254), so "rebuilt recently" ≠ "rebuilt from current bases", and
   `images` presents `Created:` with no such caveat (images.py:379).
3. `_classify_tag`'s `pinned` verdict (images.py:33) actively encourages the wrong inference:
   `httpd:2.4`, `node:24.16.0-alpine3.22` show `✓` green.
4. `whisperx`'s only base is a `Containerfile` and so escapes even the (display-only) base scan
   (images.py:303, build.py:613-618).
5. Digest comparison can false-positive "rebuilt upstream" if a locally stored image's `RepoDigests` lack the
   media-type variant the current `Accept` order negotiates (registry.py:32-40 vs images.py:267-270) — no
   fallback, and the user is told to pull.
6. Registry rate limits (HTTP 429) render as an ordinary "check failed" line with no distinction, no retry,
   no cached result (registry.py:116-132).
7. One shared `RegistryClient` across 4 threads mutates `self._tokens` without a lock (images.py:284-287,
   registry.py:117-126) — benign under the GIL but duplicate token fetches are possible; per-image
   `_lookup_remote` couples tag-list failure to the digest check (images.py:96-102).
8. `images` checks the current mode only (images.py:246): in `prod` the dev-only `simplesamlphp:v2.4.2` and
   `mongo-express:1.0.2-20-alpine3.19` pins are never digest-compared, though `images update` would rewrite
   both modes.
9. `run_image_update` prints "is the newest version" then still prompts when there are no choices
   (image_update.py:210-212) — dead-end UX, rc 1 on cancel.
10. `visp.py images` now performs outbound HTTPS by default; on an air-gapped host each image costs up to
    15 s × attempts unless `--offline` is remembered (registry.py:88, visp.py:1053).

VERDICT=PR_AUDITED

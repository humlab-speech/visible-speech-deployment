# VISP image-freshness: current procedure surface (TASK ONE)

Scope: what each `visp.py` verb answers — and fails to answer — about image freshness.
Evidence bases:

- **WT** = working tree the deployment checkout at `5346b8f` (parent `fb1e404`, predates PR 263). Line refs verified with grep on that tree.
- **MERGED** = files at merge commit `65345fe` (PR 263 = commits `a9467d0` + `657ae17`), fetched read-only from GitHub. Integrity verified: `git hash-object` of fetched `vispctl/images.py`, `registry.py`, `image_update.py` equals the API-reported blob SHAs (`4bf29d9…`, `a8f536a…`, `1054628…`).

Central concern kept throughout: a **rebuilt** `visp-*` image can still be security-stale because its `FROM` base (e.g. `node:24.16.0-alpine3.22`, `external/wsrng-server/Dockerfile:7`) is resolved from the **local podman cache** unless `--pull` is passed (`build.py:253-254`: `if pull: cmd.append("--pull")`), and official images are re-pushed under unchanged tags. Nothing on the verb surface ever compares a cached FROM base against the registry.

---

## 1. Verb-by-verb inventory

### `visp.py images` (`cmd_images`, WT `visp.py:577-582`; MERGED `visp.py:577-583`)

- WT: `display_visp_images()` + `display_network_info()`.
- MERGED adds `display_third_party_images(check_remote=not --offline)` (`visp_merged.py:582`, `--offline` flag at `:1053`).

**Answers (built layer):** built-ness per expected image (`localhost/visp-*:latest` present or `(not built)`, WT `images.py:244-274`), size, and `Created` (relative string — see §3).
**Answers (third-party, MERGED only):** for each `Image=` in `quadlets/<mode>/*.container` that isn't `localhost/` (`scan_third_party_images`, MERGED `images.py:38-53`): pulled-ness, local-vs-registry **digest** comparison (`ThirdPartyImage.digest_matches`, MERGED `images.py:86-90`; registry side via `RegistryClient.tag_digest` HEAD request, `registry.py:106-113`), "rebuilt upstream under the same tag" warning (`images.py:458`), newer-tag listings (`newer_versions`, `registry.py:218-229`), and running-container-vs-pulled-image staleness (`stale_containers`, MERGED `images.py:276-282`).

**Does NOT answer:**
- Whether a built image's **FROM base** matches what the registry serves today. The digest machinery exists but is wired only to quadlet third-party images: `RegistryClient` is used only by `_lookup_remote`/`get_third_party_images` (MERGED `images.py:93-100,285`) and `image_update.py:177-180`. Grep confirms zero registry calls in `scan_base_images` or `display_visp_images`.
- Whether a running container is on an old built image — `display_visp_images` never calls `get_stale_containers` (WT and MERGED; that call lives only in the three places of §4).
- Anything age-thresholded: `Created` is printed with no verdict (§3).

### `visp.py images base` (WT `visp.py:585-589` → `display_base_images`, WT `images.py:318-373`)

**Answers:** which `FROM` images all `docker/**`/`external/**` Dockerfiles name, which file/stage uses each, and a **pinning classification** of the tag only — `_classify_tag` (WT `images.py:13-29`): digest/dotted-version/4+-digit-date = "pinned" (green), bare-major/word tags = "unpinned" (red).

**Does NOT answer:** the actual freshness question. It is pure text parsing of Dockerfiles (`scan_base_images`, WT `images.py:168-241` — globs files, splits `FROM` lines; never touches podman or a registry). `scan_base_images` has exactly one caller in each tree: WT `images.py:323` and MERGED `images.py:529`, both inside `display_base_images`. Consequences:
- `node:24.16.0-alpine3.22` renders as ✓ "pinned" even if the tag was re-pushed with CVE fixes yesterday — pinning ≠ security freshness, and a *dotted pin whose tag moved* is precisely the dangerous case, displayed as green.
- `debian:trixie-20260406` (`docker/apache/Dockerfile:6`) is "pinned" by date, but the date is the *base snapshot*, not a check that anything is current.
- It never lists the locally cached copy of each base, its ID, or its digests.

### `visp.py images update <service>` (MERGED only; `visp_merged.py:586-593,1059-1066` → `vispctl/image_update.py`)

**Procedure:** pick a tag (offer current tag when rebuilt-upstream, `build_choices`, `image_update.py:88-104`) → `podman pull` first, abort on failure (`:236-237`) → rewrite `Image=` in **every mode's quadlet** (dev and prod, `MODES=("dev","prod")`, `:29`; `set_image_pin` `:80-85`) → `apply` (retag) or `restart` (same tag re-pull) (`:249-252`, `_apply` `:265-283`, `_restart` `:285-291`) → hint to commit the quadlet change (`:260-262`).

**Answers:** "how do I move a *pulled third-party* image to fresher content, tag and disk-consistently." Also the re-pull-current-tag path fixes the same-tag-rebuilt case.

**Does NOT answer:** anything about built images — it operates only on quadlet `Image=` lines that are third-party (`find_image_pins` `:55-69`); there is no `images update` for a `FROM` base. The operator equivalent today is `build --pull`, which reports nothing beforehand.

### `visp.py apply [service]` (WT `visp.py:400-409` → `cmd_apply`, WT `quadlets.py:207-292`)

**Answers:** "is the installed quadlet unit file drifted from `quadlets/<mode>/` templates, and is any *running container* started from an image ID older than the current `localhost/visp-<name>:latest` tag?" — and fixes both: stale-image-only services go into `restart_targets` (`quadlets.py:272-274`) and get stop/start (`:289-290`). Staleness source: `get_stale_containers` at `quadlets.py:234` (see §4).

**Does NOT answer:** whether `:latest` itself is fresh — it compares container-ID vs tag-ID only (`images.py:135-166`); a container on a two-month-old-but-current `:latest` prints "All quadlets are up to date and all containers are running the latest images" (`quadlets.py:238-243`). Base-image content is out of scope.

### `visp.py build [service|all] [--no-cache] [--pull]` (WT `visp.py:526-534`; `cmd_build` `build.py:685+`; argparse `visp.py:996-1012`)

**Answers:** builds happen, order, success/skip/fail summary (`build.py:741-765`). Post-build, calls `get_stale_containers` on the just-built services (`build.py:748`) and warns `⚠ <svc> is running an older image than :latest — './visp.py apply <svc>' to go live` (`build.py:750-755`).

**Does NOT answer:** base freshness, before or after. Without `--pull`, `FROM` resolves from cache (`build.py:243-254`); `--pull` prints one line "Building with --pull (fetch latest base images)" (`build.py:733-734`) but there is no *pre-build* check telling you which cached bases are older than the registry, and no post-build record of which base digests went in. `--no-cache` also re-resolves FROM, but again nothing reports what changed.

### `visp.py status` (WT `visp.py:147-177` → `ServiceManager.status` `service_manager.py:214-245`, then `show_quadlet_table`/`show_container_list`/`show_network_list` at `visp.py:171-177`, defined `status.py:12,59,68`)

**Answers:** three things, none about images: (1) is the systemd unit active — networks via `podman network exists` (`service_manager.py:219`), containers via `systemctl --user is-active` (`:230`); (2) quadlet *file* drift vs `quadlets/<mode>/` templates (`status.py:24` `get_quadlet_drift`) — unit-file freshness, not image freshness; (3) `podman ps -a` limited to `{{.Names}}\t{{.Status}}\t{{.Ports}}` (`status.py:62-64`) — the image column is deliberately not selected.

**Answers NOTHING about images** — provable by content: no `podman image`/image-`inspect` call anywhere in `cmd_status`, `ServiceManager.status`, or the three `status.py` helpers, and `get_stale_containers` is called from `start()` only (`service_manager.py:93`), never from `status()`. A container running a six-month-old image on a six-month-old base reads `● active` + `✓ installed` in perfect green.

### `visp.py deploy status` (WT `visp.py:595-604` → `DeployManager.check_status`, `deploy.py:650-743`)

The only verb with a real *git*-anchored freshness story for built images. Three relevant sections:

1. **CONTAINER IMAGES** (`_check_container_images`, `deploy.py:227-408`, printed at `:699-703`) — reads image **labels** via `podman inspect` (`_get_image_label`, `deploy.py:56-76`; §5):
   - `source_repo` images (apache, session-manager…): compares `git.commit` label vs source-repo HEAD and `git.commit.deploy` vs deployment-repo HEAD → `⚠ STALE` with both commit ranges (`deploy.py:275-333`).
   - `depends_on` images: compares `build.timestamp` labels of child vs parent, string-compare `child_ts < parent_ts` (`deploy.py:341-348`) — "child built before parent → child stale".
   - standalone images: `git.commit` vs deployment repo HEAD (`deploy.py:369-400`).
2. **EXTERNAL REPOS "Build Status" column** (`_get_build_status` `deploy.py:131-225`, called at `:563`): image label `git.commit`/`git.dirty` vs current source commit → UP TO DATE / STALE / DIRTY BUILD / NOT BUILT; Node builds via `.build-marker` (`deploy.py:87-124`, `build.py:478-492`).
3. **THIRD-PARTY IMAGES** (`_check_third_party_images`, `deploy.py:429-458`, printed at `:705-709`): pulled / not pulled **only** — no digest check even in MERGED; `a9467d0` only refactored it onto `scan_third_party_images` (patch hunk `@@ -432,26 +432,19 @@`).

**Answers:** "does the built image correspond to the git source currently checked out?" Plus dirty-build and child-before-parent ordering.
**Does NOT answer:** whether any of those commits' *bases* are current — a ✅ UP TO DATE row means only "rebuild wouldn't change the code layer". Third-party: only pulled-ness (the digest check lives in `images`, MERGED only, so it's a separate command).

---

## 2. Where each freshness *mechanism* lives (call-graph)

| Mechanism | Definition | Callers |
|---|---|---|
| built-ness (`podman images`, visp-* only) | `get_visp_images` WT `images.py:41-79` | `display_visp_images` only (`images.py:250`) → `images` |
| third-party quadlet scan (text) | MERGED `images.py:38-53` | `get_third_party_images` (MERGED `images.py:247`), `deploy._check_third_party_images` (a9467d0 hunk) |
| registry digest + newer tags | `registry.py:93-113,218-229` (MERGED) | `images` third-party section (`images.py:93-100,285`), `images update` (`image_update.py:177-182`). **Never** called for FROM bases. |
| ID staleness (container vs tag) | `get_stale_containers` WT `images.py:135-166` | `apply` (`quadlets.py:234`), post-`build` warning (`build.py:748`), `start` no-op warning (`service_manager.py:65-72`, called at `:93`) |
| git-commit labels | stamped `build.py:262-332`; read `deploy.py:56-76` | `deploy status` only |
| pin classification (text-only) | `_classify_tag` WT `images.py:13-29`; `scan_base_images` `:168-241` | `display_base_images` only (WT `:323`, MERGED `:529`) |

## 3. The `Created` field

`get_visp_images` asks `podman images --format {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.Created}}` (WT `images.py:53-58`) and prints `Size: … Created: …` verbatim (`images.py:256`). `{{.Created}}` in `podman images` is a **relative, coarse string** — verified live on this host: `docker.io/library/node:24.16.0-alpine3.22  5 months ago`, `alpine:latest  4 weeks ago`. So the `images` verb shows e.g. "Created: 3 weeks ago" with **no threshold, no color, no verdict**, and — decisively — "3 weeks ago" is a statement about *this machine's build event*, not about upstream content: if the FROM base in the local cache is older, the rebuild just re-wraps an old base and the timestamp looks healthy. `podman image inspect` does expose the absolute `.Created` (verified: `Created=2026-10-21 16:36:55 …` on matomo), but no code path uses it.

## 4. ID-based staleness (`get_stale_containers`)

WT `images.py:135-166`: per container-type service, `podman inspect <ctr> --format {{.ImageID}}` vs `podman image inspect localhost/visp-<name>:latest --format {{.Id}}`; mismatch ⇒ stale; not-running or not-built silently skipped (docstring `:141-143`). Wired into exactly three action paths — `apply` (`quadlets.py:234`, restarts stale at `:272-290`), post-build warning (`build.py:748-755`), `start` on an already-active unit (`service_manager.py:92-98`). NOT wired into `status`, `images`, or `deploy status`. It answers *"live container ≠ latest local tag"* — a purely local, tag-relative question; it is blind to whether the tag itself is stale, and blind to third-party images (it hardcodes `localhost/visp-*`, `images.py:158`). Third-party equivalents exist only in MERGED `get_third_party_images` (`images.py:276-282`). `visp.py status`'s container list prints `Names/Status/Ports` only (`status.py:63`), so even the container-vs-image question is invisible there.

## 5. Git-commit image labels — what podman exposes, could they anchor freshness?

Stamping at build time (`build.py`):
- `git.commit` = HEAD of `source_repo` or build context (`build.py:262-269`, `_add_git_labels` `:308-332`, label appended `:318`)
- `git.dirty=true` if source tree dirty (`:322-328`)
- `git.commit.<name>` for extra embedded repos (`:271-274`)
- `git.commit.deploy` = deployment-repo HEAD when `source_repo` set (`:276-286`)
- `build.timestamp` = local build wall-clock ISO (`:330-332`)
- **Correction to the task premise:** there is no `git.commit.date` label. `grep -rn "git.commit.date" vispctl/` → 0 hits; the date-like label is `build.timestamp`.

Exposure: labels land in image config; `podman image inspect` exposes them via `.Config.Labels` (top-level `.Labels` in inspect output) — that's how `deploy.py:56-76` reads them (`--format '{{index .Labels "git.commit"}}'`). Verified the inspect surface live on matomo (`.Config.Labels` exists, was null there; `.RepoDigests` populated — the same field MERGED's digest comparison consumes, `images.py:263-270`). No visp-* image exists on this host to inspect, so the exact live label values couldn't be shown here.

Could they anchor a freshness story for the built layer? **Only half of one.** They pin *source* freshness (what the code layer is) and are already exploited that way by `deploy status`. They say nothing about the base layer: `git.commit` is a property of the deployment/source repos, and none of the labels record which **base image ID/digest** the FROM resolved to at build time. The missing anchor is a `FROM`-digest label (or inspecting build history), which no code writes and nothing reads.

---

## 6. Transcript of commands run (read-only)

```
git -C <deployment-checkout> log --oneline -3        # HEAD = 5346b8f
grep -n "images|build|status|apply" visp.py ; sed -n '140,160p;395,415p;520,605p' visp.py
# fetched merged files, verified against API blob SHAs:
for f in images.py registry.py image_update.py; do
  curl -s "https://api.github.com/.../contents/vispctl/$f?ref=65345fe"   # base64-decode
  # sha check:
  sha=$(curl -s ...same endpoint... | jq .sha); [ "$sha" = "$(git hash-object $f)" ] && echo MATCH
done                                                               # all MATCH
curl -sL https://raw.githubusercontent.com/.../65345fe/visp.py -o visp_merged.py
curl -sL https://github.com/.../commit/{a9467d0,657ae17,65345fe}.patch   # changed-file lists; deploy.py hunk
grep -n "get_stale_containers|scan_base_images" vispctl/*.py visp.py
sed -n '255,335p' vispctl/build.py ; sed -n '200,320p' vispctl/quadlets.py ; sed -n '40,110p;214,245p' vispctl/service_manager.py
sed -n '40,790p' vispctl/deploy.py   # _get_image_label, _check_container_images, _check_third_party_images, check_status
grep -rn "git.commit.date" vispctl/          # 0 hits
grep -rn "^FROM" docker external --include="Dockerfile*" | grep -v ARCHIVE
podman images --format '{{.Repository}}:{{.Tag}} {{.Created}}'    # no visp-* on this host
podman image inspect docker.io/library/matomo:5.14.0-apache --format '{{.Created}}'
podman image inspect ... --format '{{json .Config.Labels}}' '{{json .RepoDigests}}'
```

No file on the deployment host's checkout was modified; no visp.py or mutating podman/systemd command was run (only `podman images` / `podman image inspect`).

---

## 7. Bottom line for the central concern

Across the whole verb surface (WT and MERGED), the answer to *"is my recently-rebuilt visp-session-manager actually security-current?"* is **nobody asks**. Every freshness check is either (a) tag-relative and local (`get_stale_containers`), (b) git-relative (`deploy status` labels), or (c) registry-relative but confined to third-party quadlet images (MERGED `images` digest check, `images update`). The FROM-base layer of built images is checked by no code: `scan_base_images` is display-only (`images.py:323` WT / `:529` MERGED), the `RegistryClient.tag_digest` machinery that *would* answer it exists one module away, and `--pull` is the only lever — taken on faith, unreported, unverified.

VERDICT=PROCEDURE_MAPPED


# Webclient Build & Air-gap Strategy 🔧

Summary of findings
-------------------
- A prebuilt webclient exists in `external/webclient/dist` (about **55 files**, ~**21 MB**).
- The current tooling does both: `visp_deploy.py` runs temporary Node container builds, and `docker/apache/Dockerfile` historically cloned and built the webclient inside the image. A small change now prefers a local `external/webclient/` when present.

Goals
-----
- Produce reproducible, auditable builds suitable for air-gapped (offline) image creation.
- Keep developer convenience for local iteration.
- Ensure fonts and assets are vendored (no runtime calls to Google Fonts).

Options (trade-offs) ⚖️
---------------------
1) CI-produced artifact (recommended for production)
- CI builds the webclient (Node), packages `dist/` + `fonts/` as artifacts, and performs checks (grep for `fonts.googleapis`).
- Image build in CI or on-host simply `COPY external/webclient/dist /var/www/html`.
- Pros: deterministic, cacheable, air-gap friendly; Cons: needs CI setup.

2) Multi-stage Docker build (builder-in-Dockerfile)
- Dockerfile runs a Node builder stage and copies `dist` into a smaller final image.
- Pros: single-step image build, convenient for local dev; Cons: requires network during build, less control over artifacts.

3) Commit `dist/` and fonts into repo (or release branch)
- Pros: simplest for air-gapped installs; Cons: repository growth and maintenance overhead.

Practical hybrid recommendation ✅
--------------------------------
- Use **CI to build and produce artifacts** for production releases.
  - CI job builds webclient, runs checks (fonts), and uploads `dist/` and `fonts/` as artifacts or attaches them to a release.
- Keep a **Dockerfile builder stage as fallback** for local convenience, guarded by a build ARG or an explicit `--target` so production builds remain deterministic.
- Dockerfile behavior: **prefer local `external/webclient/dist` if present**, otherwise fall back to builder stage or cloning (already implemented).

Small implementation checklist 🔧
--------------------------------
- [ ] Add a CI workflow that:
  - runs `npm ci && npm run $WEBCLIENT_BUILD` in node:20,
  - runs `grep -R "fonts.googleapis" dist` and fails on matches,
  - uploads `dist/` + `fonts/` as artifacts.
- [ ] Add `Makefile` targets for developers:
  - `make webclient-build` (local reproducible build),
  - `make vendor-webclient` (fetch CI artifact or local build into `external/webclient/`),
  - `make image` (build image with local artifacts).
- [ ] Keep Dockerfile multi-stage but add ARGs to toggle builder stage for CI vs local builds.
- [ ] Add CI smoke tests: build image, run container, curl index and verify `200`.

Quick commands (examples)
-------------------------
- Local dev build (fallback multi-stage builder):
  - podman build --build-arg BUILD_WEBCLIENT=true -t visp-apache:local .
- CI flow (producer of artifacts):
  - Step 1 (build): npm ci && npm run ${WEBCLIENT_BUILD}
  - Step 2 (verify): grep -R "fonts.googleapis" dist && exit 1
  - Step 3 (upload artifact): upload `dist/` and `fonts/`
- Image build using artifacts (CI or offline): copy artifacts into `external/webclient/` then `podman build -t visp-apache:prod .`

If you'd like, I can next:
- Add a minimal GitHub Actions workflow that builds the webclient and uploads artifacts, and/or
- Add `Makefile` targets and a small `visp_deploy` subcommand to `vendor` the artifacts locally.

---
*Document created to help choose and implement a reproducible, air-gap-capable webclient build strategy.*

# visp.py Overhaul Plan

Goal: `visp.py` should be a **thin CLI dispatcher** — argument parsing + `handler(args)` calls — with
all logic and configuration in `vispctl/`. Consistency and no duplicate registration of aliases.

---

## Step 1 — Delete dead / no-op code  ✅

**What:** Remove functions that exist but are never called or add zero logic.

- `_tail_container_logs()` — private wrapper around `_tail_container_logs_impl`; never called after `cmd_logs` was refactored to delegate wholesale to `view_logs()`.
- `_stream_podman_logs()` — same situation; dead wrapper.
- `_show_debug_info()` — same; dead wrapper.
- `_get_service_names()` — defined but never used (was probably intended for argparse `choices=`).
- The 7 `_ensure_cert` / `_is_cert_valid` / `_render_*` / `_setup_*` shim functions that each do nothing but `from vispctl.certs import X; return X(...)`. Call vispctl directly in `cmd_install`.

**Why first:** Zero functional risk; makes the file shorter and easier to work with for subsequent steps.

---

## Step 2 — Move configuration data out of `visp.py`  ✅

**What:** Move three big data structures to `vispctl/`:

| Data | New home |
|---|---|
| `SERVICES` list | `vispctl/service.py` → `DEFAULT_SERVICES` |
| `OPTIONAL_SERVICE_ENV_FLAGS` dict | `vispctl/service.py` → `OPTIONAL_SERVICE_ENV_FLAGS` |
| `CONTAINER_LOG_FILES` dict | `vispctl/logs.py` → `CONTAINER_LOG_FILES` |
| `BUILD_CONFIGS` dict | `vispctl/build.py` → `BUILD_CONFIGS` |
| `NODE_BUILD_CONFIGS` dict | `vispctl/build.py` → `NODE_BUILD_CONFIGS` |

`visp.py` imports them instead of defining them. `BuildManager` and `ImageManager` use the
module-level defaults when no override is passed (they already accept optional dicts).

**Why second:** Everything else depends on these being importable from `vispctl/`. Doing this early
means later steps can import cleanly.

---

## Step 3 — Standardize `Runner` usage  ✅

**What:**
- Remove the module-level convenience wrappers `run()`, `run_quiet()`, `systemctl()`, `journalctl()` in `visp.py`.
- Replace all `ServiceManager(Runner(), ...)` calls with `ServiceManager(RUNNER, ...)` using the single module-level `RUNNER`.
- All call sites that created ad-hoc `runner = Runner()` now use `RUNNER`.

**Why third:** Prerequisite for later steps that touch those call sites. Also makes the
Runner/subprocess strategy obvious — there is one instance, one place to swap for testing.

---

## Step 4 — Fix `cmd_apply` to use `install_quadlets()`  ✅

**What:** `cmd_apply` contains an inline for-loop that reads/renders/writes quadlet files
itself, bypassing `vispctl/install.py`'s `install_quadlets()`. Replace it with a call to
`install_quadlets(force=True, services=to_update)`.

**Why:** DRY. Any future changes to install logic (validation, dry-run, error handling) only
need to happen in one place.

---

## Step 5 — Move build version-check logic into `BuildManager`  ✅

**What:** Extract the ~60-line version drift check block from `cmd_build` (lines ~1090–1145)
into a new `BuildManager.check_version_drift(ordered, mode)` method that returns
`(warnings, is_blocking)`. `cmd_build` calls it and acts on the result.

**Why:** `cmd_build` shouldn't import `GitRepository` and `ComponentConfig` directly. The
build subsystem should own its own pre-flight checks.

---

## Step 6 — Standardize subcommand dispatch with `set_defaults(func=...)`  ✅

**What:**
- On every `add_parser(...)` call, add `.set_defaults(func=cmd_xxx)`.
- Replace the giant `cmd_map` dict at the bottom of `main()` with `args.func(args)`.
- Remove all alias keys from `cmd_map` (they become implicit via `set_defaults`).
- Flatten the deploy sub-subcommand dispatch: move `cmd_deploy_*` shims into
  `vispctl/deploy.py` as a dispatcher method, or keep them but call via `set_defaults`.

**Why:** Adding a new command or alias currently requires editing two places (parser + cmd_map).
With `set_defaults`, you edit one place only.

---

## Step 7 — Fix hardcoded network names in `cmd_uninstall`  ✅

**What:** Replace the hardcoded `visp_networks = ["systemd-visp-net", "systemd-octra-net"]` list
with one derived from `NETWORK_SERVICES`: systemd prefixes network unit names with `systemd-`,
so the name is `"systemd-" + svc.name`.

**Why:** Adding a new `.network` quadlet currently requires editing `cmd_uninstall` manually.

---

## Step 8 — Fix `cmd_debug` args mutation  ✅

**What:** `cmd_debug` mutates the `args` namespace in-place by adding attributes and then calls
`cmd_logs(args)`. Replace with an explicit `argparse.Namespace` construction so it's clear
exactly what is being passed.

**Why:** Fragile — if `view_logs()` ever reads an attribute that `cmd_debug` forgot to set,
it silently gets `None`. Explicit namespace makes the contract obvious.

---

## Step 9 — Remove duplicate BUILD_CONFIGS / NODE_BUILD_CONFIGS definitions  ✅

**What:** Step 2 moved `BUILD_CONFIGS`, `NODE_BUILD_CONFIGS`, `BUILDABLE_SERVICES`, and
`ALL_BUILDABLE` to `vispctl/build.py` and added imports at the top of `visp.py`. However the
original inline definitions (lines ~761–857) were never removed. The import at line 48 is
immediately overridden by the re-definition 700 lines later. Delete the inline duplicate block.

**Why:** This is a live bug — edits to `vispctl/build.py` are silently ignored because the
in-file copy wins. Also ~100 lines of dead duplication.

---

## Step 10 — Move quadlet/mode helpers out of `visp.py`  ✅

**What:** Four utility functions at the top of `visp.py` belong in `vispctl/quadlets.py`:

| Function | New home |
|---|---|
| `render_quadlet_template(content)` | `vispctl/quadlets.py` |
| `get_current_mode()` | `vispctl/quadlets.py` |
| `set_current_mode(mode)` | `vispctl/quadlets.py` |
| `get_quadlets_dir(mode)` | `vispctl/quadlets.py` |

`visp.py` imports and re-exports them for any call sites that use the module-level names.

**Why:** These are pure quadlet/mode logic with no dependency on CLI state. Moving them makes
`vispctl/quadlets.py` the single place to look for quadlet file handling.

---

## Step 11 — Move service resolver helpers to `vispctl/service.py`  ✅

**What:** Three private helpers at the bottom of `visp.py` are service-resolution logic,
not CLI glue:

| Function | What it does |
|---|---|
| `_get_disabled_optional_services()` | Reads `.env`, filters `OPTIONAL_SERVICE_ENV_FLAGS` |
| `_get_runtime_services(include_disabled)` | Filters `DEFAULT_SERVICES` for current mode/env |
| `_resolve_services(service_arg, include_disabled)` | Resolves `"all"` / name → `list[Service]` |

Move to `vispctl/service.py` as module-level functions. `visp.py` imports them.
`_container_services()` can stay (it's a trivial one-liner used only in `cmd_start`/`cmd_restart`).

**Why:** Any future code that needs to resolve services (e.g. a `vispctl` subcommand) currently
can't do so without importing from `visp.py` (a CLI script). Logic belongs in the library.

---

## Verification after each step

```bash
# After each step, run:
./visp.py status          # basic smoke test
./visp.py build --list    # verify BUILD_CONFIGS accessible
./visp.py deploy status --no-fetch
pre-commit run --all-files
```

---

## Step 12 — Extract `cmd_status` display logic into `vispctl/status.py`  ✅

**What:** `cmd_status` (93 lines) contains three distinct display blocks:
1. Quadlet unit link table (symlink exists? active? enabled?)
2. Container image table (image present? size? age?)
3. Live container listing (podman ps output)

Extract these into functions in a new `vispctl/status.py`. `cmd_status` becomes:
```python
def cmd_status(args):
    from vispctl.status import show_container_list, show_quadlet_table, show_image_table
    show_container_list(RUNNER)
    show_quadlet_table(get_current_mode(), RUNNER)
    show_image_table(RUNNER)
```

**Why:** Status display logic is independent of CLI plumbing. A future `vispctl` API or test
can call `show_image_table()` directly without invoking the argparse entrypoint.

---

## Step 13 — Move `_resolve_services` fully to `vispctl/service.py`  ✅

**What:** `_resolve_services(service_arg, include_disabled)` still lives in `visp.py` as a
4-line wrapper that re-exports `vispctl.service.resolve_services`. Currently the error message
formatting (for unknown service names, or requesting a disabled optional service) still
happens inside the visp.py wrapper. Move the full implementation into
`vispctl/service.py:resolve_services()` and delete the wrapper entirely from `visp.py`.

**Why:** Any code that needs to resolve a service argument (e.g. the `sd` subcommand, or a
future REST API shim) currently can't do so without loading the CLI script.

---

## Step 14 — Move `cmd_fix_permissions` post-check into `PermissionsManager`  ✅

**What:** `cmd_fix_permissions` (104 lines) has two phases:
1. Path defaulting + dry-run reporting (~30 lines) — legitimate CLI glue, stays.
2. Ownership verification loop after `pm.fix_permissions()` runs (~40 lines) — pure
   `PermissionsManager` logic that verifies the fix worked and prints discrepancies.

Extract the post-check block into `PermissionsManager.verify(paths)` or fold it into
`fix_permissions(dry_run=False, verify=True)`. `cmd_fix_permissions` just calls `pm.fix()`.

**Why:** The verification logic is reusable (e.g. could be called from `cmd_install` as a
post-install sanity check without going through the CLI).

---

## Order summary

| # | Step | Risk | Status |
|---|---|---|---|
| 1 | Delete dead code | Zero | ✅ |
| 2 | Move config data to vispctl | Low | ✅ |
| 3 | Standardize Runner | Low | ✅ |
| 4 | Fix cmd_apply | Low | ✅ |
| 5 | Move version-check to BuildManager | Medium | ✅ |
| 6 | Standardize dispatch (set_defaults) | Medium | ✅ |
| 7 | Fix hardcoded network names | Low | ✅ |
| 8 | Fix cmd_debug args mutation | Low | ✅ |
| 9 | Remove duplicate BUILD_CONFIGS | Zero | ✅ |
| 10 | Move quadlet/mode helpers to vispctl | Low | ✅ |
| 11 | Move service resolver helpers to vispctl | Low | ✅ |
| 12 | Extract cmd_status display into vispctl/status.py | Low | ✅ |
| 13 | Move _resolve_services fully to vispctl/service.py | Low | ✅ |
| 14 | Move fix_permissions post-check into PermissionsManager | Low | ✅ |

# Version Management Guide

## Overview

Which commit of each external component (the repositories under `external/`) goes into
VISP is pinned in the `components` section of `release.json`, the release manifest:

```json
"components": {
  "session-manager": {"ref": "03caa07…"},
  …
}
```

Each `ref` is a full commit SHA. Where a component comes from (its git URL, whether it
has submodules) is not in the file; it lives in `DEFAULT_VERSIONS_CONFIG` in
`vispctl/versions.py`.

`release.json` also pins the third-party images and records the VISP version. Cutting a
release and upgrading a server are described in AGENTS.md, under "VISP releases and
upgrades". This guide covers only the component pins.

## Quick Reference

```bash
./visp.py deploy status            # repos, images and drift against the pins
./visp.py deploy update            # prod: check out the pins; dev: pull the latest commit
./visp.py deploy update --release  # check out the pins, whatever the mode
./visp.py deploy update --latest   # pull the latest commit, whatever the mode
./visp.py deploy lock webclient    # pin webclient to its checked-out commit
./visp.py deploy lock --all        # pin every component (same as 'release pin --all')
./visp.py release pin webclient --remote  # pin to the tip of origin's default branch
./visp.py deploy rollback --all    # check every component out at its pin
./visp.py deploy unlock webclient  # remove webclient's pin
```

A pin is a commit, so `deploy lock` refuses a checkout with uncommitted changes, and
warns when the commit is on no remote branch (push it, or other servers cannot check
it out).

In prod mode, `./visp.py build` refuses to build webclient or container-agent when the
checkout is not at its pin.

---

## Workflows

### Develop against the latest code (dev mode)

```bash
./visp.py deploy update   # pulls the latest commit of each branch
./visp.py deploy status   # review what changed
./visp.py build           # rebuild what is stale
```

### Move one component to a newer commit

```bash
cd external/webclient && git pull && cd ../..   # or check out the commit you want
./visp.py build webclient
./visp.py restart apache

# Works → pin it, and commit release.json
./visp.py deploy lock webclient
git add release.json
git commit -m "chore(release): pin webclient to <sha> — <why>"

# Broken → back to the pinned commit
./visp.py deploy rollback webclient
./visp.py build webclient
```

### Roll back after a bad update

```bash
./visp.py deploy rollback --all   # check every component out at its pin
./visp.py build                   # rebuild from the rolled-back source
./visp.py restart all
```

### Unpinned components

`deploy unlock` sets a component's `ref` to `null`; `deploy update` then pulls its
latest commit even in prod mode. Use it only while working: `./visp.py release check`
and the test suite fail while any component is unpinned, so pin it again before
committing `release.json`.

---

## Related Documentation

- AGENTS.md, "VISP releases and upgrades" — `release create`, `upgrade`, `release check`
- [VERSION_CHECKING.md](VERSION_CHECKING.md) — How the status system works (image labels, drift detection)
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md) — Backup and restore procedures

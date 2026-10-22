"""Base-image freshness for locally built ``visp-*`` images.

A ``FROM`` tag can be re-pushed upstream and ``podman build`` defaults to
``--pull=missing``, so a rebuild can silently re-bake a stale base. Per built
image and per base this module answers: has the content behind that tag moved
since the image was built?

Verdicts are exact only when ``.visp/base-lock.json`` records what the build
consumed (keyed by built image ID). Without a record the comparison falls back
to the base copy in the local store and is labelled ``heuristic`` — never a
guessed "fresh".
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .build import BUILD_CONFIGS
from .config import get_config
from .images import _classify_tag
from .registry import ImageRef, RegistryClient
from .runner import Colors, color

FRESH = "fresh"
MOVED = "moved"
HEURISTIC = "heuristic"
NEVER_CHECKED = "never-checked"
OFFLINE = "offline"
IMMOBILE = "immobile"

LOCK_FILE = Path(".visp") / "base-lock.json"


def load_lock(project_dir: Path) -> dict:
    """Read the base-lock file; missing or unparsable degrades to no record."""
    try:
        data = json.loads((Path(project_dir) / LOCK_FILE).read_text(errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_lock(project_dir: Path, lock: dict) -> None:
    """Write the base-lock file (gitignored machine state, not a committed pin)."""
    path = Path(project_dir) / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lock, indent=1, sort_keys=True))


def record_build(project_dir: Path, built_image_id: str, bases: dict, built_at: str, pull_policy: str) -> dict:
    """Record the bases *built_image_id* consumed. The caller saves the returned lock."""
    lock = load_lock(project_dir)
    lock.setdefault("images", {})[built_image_id] = {
        "built_at": built_at,
        "pull_policy": pull_policy,
        "bases": bases,
    }
    return lock


def _normalize_ref(ref: str) -> str:
    """Drop the default registry prefixes so podman names and FROM lines agree."""
    for prefix in ("docker.io/library/", "docker.io/"):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref


def _parse_stages(text: str) -> list[dict]:
    """Parse FROM/COPY lines into ``{name, ref, copies}`` stages, in file order.

    ``ref`` is None for a stage built FROM an earlier stage and for a FROM
    pinned by ``@sha256:``, which cannot move and needs no verdict. ``from_stage``
    keeps the earlier stage's name so the real registry base can be resolved
    through the chain (apache: production <- base <- debian).
    """
    stages: list[dict] = []
    defined: set[str] = set()
    for line in text.splitlines():
        parts = line.strip().split()
        if not parts or parts[0].startswith("#"):
            continue
        verb = parts[0].upper()
        if verb == "FROM":
            args = [p for p in parts[1:] if not p.startswith("--")]
            if not args:
                continue
            ref = args[0]
            name = args[2] if len(args) >= 3 and args[1].upper() == "AS" else None
            from_stage = None
            if "@sha256:" in ref:
                ref = None
            elif ref in defined:
                ref, from_stage = None, ref
            else:
                ref = _normalize_ref(ref)
            stages.append({"name": name, "ref": ref, "from_stage": from_stage, "copies": []})
            if name:
                defined.add(name)
        elif verb == "COPY" and stages:
            stages[-1]["copies"] += [p.partition("=")[2] for p in parts if p.startswith("--from=")]
    return stages


def _resolved_ref(stages: list[dict], i: int) -> str | None:
    """Registry ref at the head of stage ``i``'s internal FROM chain, if any."""
    while stages[i]["ref"] is None and stages[i]["from_stage"]:
        prev = [j for j, s in enumerate(stages[:i]) if s["name"] == stages[i]["from_stage"]]
        if not prev:
            return None
        i = prev[-1]  # from_stage always names an earlier stage: the walk terminates
    return stages[i]["ref"]


def _referenced_stages(stages: list[dict], target: int) -> set[int]:
    """Indices of the stages *target* pulls artifacts from, transitively."""
    by_name = {stage["name"]: i for i, stage in enumerate(stages) if stage["name"]}
    found: set[int] = set()
    queue = [target]
    while queue:
        for src in stages[queue.pop()]["copies"]:
            i = by_name.get(src)
            if i is None and src.isdigit() and int(src) < len(stages):
                i = int(src)  # COPY --from=<zero-based stage index>
            if i is not None and i not in found:
                found.add(i)
                queue.append(i)
    return found


def stage_bases(project_dir: Path, cfg: dict) -> list[tuple[str, str, str | None]]:
    """Return ``[(ref, role, stage name)]`` of the registry bases of one build config.

    The Dockerfile is located exactly as ``build.py`` composes its ``-f``
    argument. The runtime base is the target stage's (or the last stage's) FROM,
    resolved through internal ``FROM <earlier-stage>`` chains to the registry
    ref at the head; build bases are the bases of the stages the target
    COPY --from's.
    """
    dockerfile = cfg.get("dockerfile", "Dockerfile")
    if dockerfile.startswith("./"):
        path = Path(project_dir) / dockerfile
    else:
        path = Path(project_dir) / cfg.get("context", ".") / dockerfile
    path = path.resolve()
    if not path.is_file():
        return []

    stages = _parse_stages(path.read_text(errors="replace"))
    if not stages:
        return []

    target = len(stages) - 1
    if cfg.get("target"):
        target = next((i for i, s in enumerate(stages) if s["name"] == cfg["target"]), target)

    items: list[tuple[str, str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    for i in sorted(_referenced_stages(stages, target) - {target}):
        ref = _resolved_ref(stages, i)
        if ref and (ref, "build") not in seen:
            seen.add((ref, "build"))
            items.append((ref, "build", stages[i]["name"]))
    runtime = _resolved_ref(stages, target)
    if runtime:
        items.append((runtime, "runtime", stages[target]["name"]))
    return items


def local_image_state(runner) -> tuple[dict[str, dict], dict[str, dict]]:
    """One ``podman images`` call, split into (bases, built visp images).

    ``bases`` maps a normalized ``name:tag`` to the local id and every
    ``@sha256:`` tail podman recorded; ``built`` maps ``localhost/visp-<x>``
    (without ``:latest``) to the id of the image we built.
    """
    rc, stdout, _ = runner.run_quiet(["podman", "images", "--format", "json"])
    try:
        entries = json.loads(stdout) if rc == 0 and stdout else []
    except ValueError:
        entries = []

    bases: dict[str, dict] = {}
    built: dict[str, dict] = {}
    for entry in entries:
        digests = {d.partition("@")[2] for d in entry.get("RepoDigests") or []}
        for name in entry.get("Names") or []:
            ref = _normalize_ref(name)
            if ref.startswith("localhost/visp-") and ref.endswith(":latest"):
                built.setdefault(ref[: -len(":latest")], {"id": entry.get("Id")})
            else:
                bases.setdefault(ref, {"id": entry.get("Id"), "digests": digests})
    return bases, built


def is_snapshot_ref(ref: str) -> bool:
    """True for a dated snapshot tag (``debian:trixie-20260406``): pinned and immovable."""
    last = ref.rsplit("/", 1)[-1]
    tag = last.rsplit(":", 1)[1] if ":" in last else "latest"
    return _classify_tag(tag) == "pinned" and bool(re.search(r"20\d{6}", tag))


@dataclass(frozen=True)
class BaseFreshness:
    """One base of one built image, with the evidence behind its verdict."""

    service: str
    ref: str
    role: str  # runtime | build
    stage_name: str | None
    built_image_id: str | None
    recorded_digests: frozenset[str]
    local_digests: frozenset[str]
    remote_digest: str | None
    remote_error: str | None
    checked_at: str | None
    check_source: str  # live | cache | none
    snapshot: bool | None = None  # defaults to is_snapshot_ref(ref)

    def __post_init__(self) -> None:
        if self.snapshot is None:
            object.__setattr__(self, "snapshot", is_snapshot_ref(self.ref))

    @property
    def state(self) -> str:
        """Six-state verdict; the first match wins (silence never reads as safe)."""
        if self.check_source == "none":
            return NEVER_CHECKED
        if self.check_source == "cache" and self.remote_error:
            return OFFLINE
        return self._digest_verdict()

    def _digest_verdict(self) -> str:
        """Verdict implied by the digest evidence alone, ignoring how fresh the check was."""
        if self.recorded_digests and self.remote_digest:
            if self.remote_digest in self.recorded_digests:
                return IMMOBILE if self.snapshot else FRESH
            return MOVED
        return HEURISTIC

    @property
    def remediation(self) -> str:
        return f"./visp.py build {self.service} && ./visp.py apply {self.service}"


def _lookup_remote(client: RegistryClient, ref: str) -> tuple[str, str | None, str | None]:
    """Return (ref, digest, error): failures are recorded, not raised."""
    try:
        return ref, client.tag_digest(ImageRef.parse(ref)), None
    except Exception as e:  # malformed ref, API drift — record, never abort the whole report
        return ref, None, str(e)


def get_base_freshness(
    runner,
    project_dir: Path | None = None,
    *,
    check_remote: bool = True,
    client: RegistryClient | None = None,
    build_configs: dict | None = None,
) -> list[BaseFreshness]:
    """Freshness verdict of every registry base behind a built visp image."""
    project_dir = Path(project_dir) if project_dir else get_config().project_dir
    configs = build_configs if build_configs is not None else BUILD_CONFIGS

    lock = load_lock(project_dir)
    records = lock.get("images") or {}
    checks = lock.setdefault("checks", {})
    local_bases, built = local_image_state(runner)

    entries: list[tuple[str, str, str, str | None]] = []
    ids: dict[str, str] = {}
    for service, cfg in configs.items():
        key = f"localhost/{cfg['image']}"
        if key not in built:
            continue  # not built here — nothing to say about it
        ids[service] = built[key]["id"]
        entries += [(service, ref, role, stage) for ref, role, stage in stage_bases(project_dir, cfg)]

    refs = list(dict.fromkeys(ref for _, ref, _, _ in entries))
    remote: dict[str, str] = {}
    errors: dict[str, str] = {}
    if check_remote and refs:
        client = client or RegistryClient()
        with ThreadPoolExecutor(max_workers=4) as pool:
            for ref, digest, error in pool.map(lambda r: _lookup_remote(client, r), refs):
                (errors if error else remote)[ref] = error or digest
        if remote:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for ref, digest in remote.items():
                checks[ref] = {"remote_digest": digest, "checked_at": now}
            try:
                save_lock(
                    project_dir, lock
                )  # cached checks are best-effort: a read-only or full disk must not crash a read command
            except OSError:
                pass

    items: list[BaseFreshness] = []
    for service, ref, role, stage in entries:
        record = ((records.get(ids[service]) or {}).get("bases") or {}).get(ref) or {}
        check = checks.get(ref) or {}
        if ref in remote:
            digest, source, checked_at = remote[ref], "live", checks[ref]["checked_at"]
        elif check.get("remote_digest"):
            digest, source, checked_at = check["remote_digest"], "cache", check.get("checked_at")
        else:
            digest, source, checked_at = None, "none", check.get("checked_at")
        items.append(
            BaseFreshness(
                service=service,
                ref=ref,
                role=role,
                stage_name=stage,
                built_image_id=ids[service],
                recorded_digests=frozenset(record.get("digests") or ()),
                local_digests=frozenset((local_bases.get(ref) or {}).get("digests") or ()),
                remote_digest=digest,
                remote_error=errors.get(ref),
                checked_at=checked_at,
                check_source=source,
            )
        )
    return items


def fan_out(items: list[BaseFreshness]) -> dict[str, list[tuple[str, str]]]:
    """Map each base ref to the sorted unique (service, role) pairs that use it."""
    out: dict[str, list[tuple[str, str]]] = {}
    for b in items:
        pairs = out.setdefault(b.ref, [])
        if (b.service, b.role) not in pairs:
            pairs.append((b.service, b.role))
    return {ref: sorted(pairs) for ref, pairs in out.items()}


def format_base_line(b: BaseFreshness) -> str:
    """The indented one-line verdict printed under a built image."""
    head = f"  Base:  {b.ref}  {b.role}" + (f" ({b.stage_name})" if b.stage_name else "")
    state = b.state

    if state in (FRESH, IMMOBILE):
        label = "✓ fresh" if state == FRESH else "✓ immobile (dated snapshot)"
        return color(f"{head}  {label}", Colors.GREEN)
    if state == MOVED:
        built_on = min(b.recorded_digests, default="?")[:19]
        as_of = f" (as of {b.checked_at[:10]})" if b.checked_at else ""
        return color(f"{head}  MOVED  built on {built_on}, tag now {b.remote_digest[:19]}{as_of}", Colors.YELLOW)
    if state == OFFLINE:
        verdict = {FRESH: "fresh", MOVED: "MOVED", IMMOBILE: "immobile (dated snapshot)"}.get(
            b._digest_verdict(), HEURISTIC
        )
        as_of = b.checked_at[:10] if b.checked_at else "?"
        return color(f"{head}  offline — cached {verdict} as of {as_of}", Colors.YELLOW)
    if state == HEURISTIC:
        if b.remote_digest and b.remote_digest in b.local_digests:
            evidence = "the base you have matches registry content today"
        elif b.remote_digest and b.local_digests:
            evidence = f"the base you have is {min(b.local_digests)[:19]}, tag now {b.remote_digest[:19]}"
        else:
            evidence = "the base is not in the local store"
        return color(
            f"{head}  {HEURISTIC} — {evidence}; compares the local base copy, not the baked-in content",
            Colors.YELLOW,
        )
    return color(f"{head}  {NEVER_CHECKED} — no verdict", Colors.DIM)


def format_summary(items: list[BaseFreshness], *, checked: bool) -> list[str]:
    """The not-fresh count, one remediation command, the base fan-out, the caveats."""
    if not items:
        return []

    lines: list[str] = []
    stale = [b for b in items if b.state in (MOVED, HEURISTIC)]
    if stale:
        lines.append(
            color(
                f"{len({b.service for b in stale})} of {len({b.service for b in items})} images are NOT FRESH "
                "(base content moved behind a tag "
                "since they were built, or only the local copy can be compared)",
                Colors.YELLOW,
            )
        )
        services = " ".join(dict.fromkeys(b.service for b in stale))
        lines.append("Rebuild (this build refreshes the base; a plain rebuild would not):")
        lines.append(f"    ./visp.py build {services} && ./visp.py apply {services}")
        lines.append("Bases and who they implicate:")
        for ref, pairs in fan_out(stale).items():
            lines.append(f"    {ref} → " + ", ".join(f"{svc} ({role})" for svc, role in pairs))

    dates = [b.checked_at for b in items if b.checked_at]
    if checked and dates:
        lines.append(f"Registry last checked: {max(dates)[:19].replace('T', ' ')}")
    else:
        lines.append("Registry not checked (--offline).")

    lines.append(color("fresh means the registry digest matches, not that there are no CVEs.", Colors.DIM))
    return lines

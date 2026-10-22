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
from .runner import Colors, color, printable

FRESH = "fresh"
MOVED = "moved"
HEURISTIC = "heuristic"
NEVER_CHECKED = "never-checked"
OFFLINE = "offline"
IMMOBILE = "immobile"

LOCK_FILE = Path(".visp") / "base-lock.json"

# What a FROM ref may contain. Anything else (control bytes, ``$`` variables,
# quotes, spaces) is not a ref we can ask a registry about, only text to show.
_REF_CHARS = re.compile(r"[\w.+/@:-]+")
_STAGE_NAME = re.compile(r"[\w.-]+")


def fetchable(ref: str) -> bool:
    """True only for a plain ref the registry client may be handed; the rest is shown, never fetched."""
    return bool(_REF_CHARS.fullmatch(ref))


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
    r"""Parse FROM/COPY lines into ``{name, ref, copies}`` stages, in file order.

    ``ref`` is None for a stage built FROM an earlier stage and for a FROM
    pinned by ``@sha256:``, which cannot move and needs no verdict. ``from_stage``
    keeps the earlier stage's name so the real registry base can be resolved
    through the chain (apache: production <- base <- debian).

    A line we cannot read reliably — a ref outside ``[\w.+/@:-]`` (a ``$`` variable,
    control bytes) or a stage name outside ``[\w.-]`` — becomes one literal entry:
    it is reported verbatim (sanitized at display) and never fetched, because a
    guessed ref would be printed as a verdict and sent to a registry.
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
            if (ref and not _REF_CHARS.fullmatch(ref)) or (name and not _STAGE_NAME.fullmatch(name)):
                ref, name, from_stage = " ".join(args), None, None
            stages.append({"name": name, "ref": ref, "from_stage": from_stage, "copies": []})
            if name:
                defined.add(name)
        elif verb == "COPY" and stages:
            stages[-1]["copies"] += [p.partition("=")[2] for p in parts if p.startswith("--from=")]
    return stages


def _from_chain(stages: list[dict], i: int) -> list[int]:
    """Indices of stage ``i`` and every stage it inherits FROM, chain head last."""
    chain = [i]
    while stages[chain[-1]]["ref"] is None and stages[chain[-1]]["from_stage"]:
        prev = [j for j, s in enumerate(stages[: chain[-1]]) if s["name"] == stages[chain[-1]]["from_stage"]]
        if not prev:
            break  # a FROM naming an unknown stage has no registry base we could name
        chain.append(prev[-1])  # from_stage always names an earlier stage: the walk terminates
    return chain


def _resolved_ref(stages: list[dict], i: int) -> str | None:
    """Registry ref at the head of stage ``i``'s internal FROM chain, if any."""
    return stages[_from_chain(stages, i)[-1]]["ref"]


def _referenced_stages(stages: list[dict], target: int) -> set[int]:
    """Indices of the stages whose artifacts *target* ends up carrying, transitively.

    The walk starts from the target's whole ``FROM <earlier-stage>`` chain, not from
    the target alone: a stage inherited via FROM also inherits what that stage
    ``COPY --from``'d, so an artifact pulled in several stages up ships in the target
    as well. The inherited stages themselves are excluded — their registry base is
    reported as the runtime base, not twice.
    """
    by_name = {stage["name"]: i for i, stage in enumerate(stages) if stage["name"]}
    inherited = set(_from_chain(stages, target))
    found: set[int] = set()
    queue = list(inherited)
    while queue:
        for src in stages[queue.pop()]["copies"]:
            i = by_name.get(src)
            if i is None and src.isdigit() and int(src) < len(stages):
                i = int(src)  # COPY --from=<zero-based stage index>
            if i is not None and i not in found:
                found.add(i)
                queue.append(i)
    return found - inherited


def stage_bases(project_dir: Path, cfg: dict) -> list[tuple[str, str, str | None]]:
    """Return ``[(ref, role, stage name)]`` of the registry bases of one build config.

    The Dockerfile is located exactly as ``build.py`` composes its ``-f``
    argument. The runtime base is the target stage's (or the last stage's) FROM,
    resolved through internal ``FROM <earlier-stage>`` chains to the registry
    ref at the head; build bases are the bases of the stages the target inherits
    artifacts from, directly or through a stage it inherits FROM.
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
    for i in sorted(_referenced_stages(stages, target)):
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
        """Six-state verdict; the first match wins (silence never reads as safe).

        A cached check is *always* qualified as offline, whether or not a fetch was
        attempted and failed: a cached digest records what the tag served on
        ``checked_at``, never what it serves today — so a deliberate ``--offline``
        run over a stale cache may not print a bare green ``fresh``. ``remote_error``
        feeds only the yellow registry line of :func:`format_summary`.
        """
        if self.check_source == "none":
            return NEVER_CHECKED
        if self.check_source == "cache":
            return OFFLINE
        return self._digest_verdict()

    def _digest_verdict(self) -> str:
        """Verdict implied by the digest evidence alone, ignoring how fresh the check was."""
        if self.recorded_digests and self.remote_digest:
            if self.remote_digest in self.recorded_digests:
                return IMMOBILE if self.snapshot else FRESH
            return MOVED
        return HEURISTIC

    def remediation(self, applyable: set[str] | None = None) -> str:
        """The commands that actually finish the job for this base.

        ``./visp.py apply`` takes exactly one service and refuses names that have no
        runtime quadlet (``jupyter-session`` among them), so the apply half appears
        only for a service the caller confirmed has one. ``applyable=None`` means
        nobody confirmed anything: name the shape, never guess a target.
        """
        if applyable is None:
            return f"./visp.py build {self.service}  (then ./visp.py apply <service>, one service per run)"
        if self.service in applyable:
            return f"./visp.py build {self.service} && ./visp.py apply {self.service}"
        return f"./visp.py build {self.service}  (no quadlet to apply — picked up when the next session starts)"


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

    refs = [ref for ref in dict.fromkeys(ref for _, ref, _, _ in entries) if fetchable(ref)]
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


def needs_rebuild(b: BaseFreshness) -> bool:
    """True when a rebuild could change the image — on the evidence we actually have.

    Deliberately reads the digest verdict, not ``state``: an offline (cached)
    verdict must keep counting here, or an air-gapped run would quietly lose the
    not-fresh count and read as all-clear. Everything except a confirmed
    fresh/immobile is treated as stale (unknown fails safe), but a base with no
    registry evidence at all — ``never-checked``, whether from ``--offline``
    without a cache or from a total outage — proves nothing and is excluded: it is
    named by its own line, not counted as a move.
    """
    return b.remote_digest is not None and b._digest_verdict() not in (FRESH, IMMOBILE)


def is_moved(b: BaseFreshness) -> bool:
    """True when the digests say the tag moved, offline qualification aside.

    Gates the per-base rebuild hint: a heuristic row must not print a
    ``build && apply`` as if a move were confirmed, and a cached move still must.
    """
    return b._digest_verdict() == MOVED


def _images(count: int) -> str:
    """``1 image`` / ``2 images``, so a headline never reads ``1 images``."""
    return f"{count} image" + ("" if count == 1 else "s")


def format_base_line(b: BaseFreshness) -> str:
    """The indented one-line verdict printed under a built image."""
    head = f"  Base:  {printable(b.ref)}  {b.role}" + (f" ({printable(b.stage_name)})" if b.stage_name else "")
    state = b.state

    if state in (FRESH, IMMOBILE):
        label = "✓ fresh" if state == FRESH else "✓ immobile (dated snapshot)"
        return color(f"{head}  {label}", Colors.GREEN)
    if state == MOVED:
        built_on = min(b.recorded_digests, default="?")[:19]
        as_of = f" (as of {b.checked_at[:10]})" if b.checked_at else ""
        return color(f"{head}  MOVED  built on {built_on}, tag now {b.remote_digest[:19]}{as_of}", Colors.YELLOW)
    if state == OFFLINE:
        # Nothing was contacted to confirm any of this, so every cached verdict —
        # the guess flavour included — carries the as-of date and no "today".
        verdict = {
            FRESH: "fresh",
            MOVED: "MOVED",
            IMMOBILE: "immobile (dated snapshot)",
            HEURISTIC: f"{HEURISTIC} (compares the local base copy, not the baked-in content)",
        }[b._digest_verdict()]
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


def format_summary(items: list[BaseFreshness], *, checked: bool, applyable: set[str] | None = None) -> list[str]:
    """The not-fresh count, the remediation commands, the base fan-out, the caveats.

    ``applyable`` names the services ``./visp.py apply`` accepts (the ones with a
    runtime quadlet). The build is batched into one command because it takes many
    services; apply is not, because it takes exactly one.
    """
    if not items:
        return []

    lines: list[str] = []
    stale = [b for b in items if needs_rebuild(b)]
    if stale:
        moved = {b.service for b in stale if is_moved(b)}
        guessed = {b.service for b in stale} - moved
        lines.append(
            color(f"{len(moved | guessed)} of {len({b.service for b in items})} images are NOT FRESH", Colors.YELLOW)
        )
        if moved:
            lines.append(
                color(
                    f"    {_images(len(moved))}: base moved behind the tag since the build baked it in", Colors.YELLOW
                )
            )
        if guessed:
            lines.append(
                color(
                    f"    {_images(len(guessed))}: no build record (heuristic) — a rebuild with --pull=newer"
                    " makes the verdict exact",
                    Colors.YELLOW,
                )
            )
        services = list(dict.fromkeys(b.service for b in stale))
        lines.append("Rebuild (a build now refreshes the base: it re-resolves each tag with --pull=newer):")
        lines.append(f"    ./visp.py build {' '.join(services)}")
        if applyable is not None:  # an empty set is an answer too: nothing here is applyable
            for svc in services:
                lines.append(
                    f"    ./visp.py apply {svc}"
                    if svc in applyable
                    else f"    {svc} has no quadlet to apply — picked up when the next session starts"
                )
        else:
            lines.append("    then ./visp.py apply <service> per runtime service you run (apply takes one service)")
        lines.append("Bases and who they implicate:")
        for ref, pairs in fan_out(stale).items():
            lines.append(f"    {printable(ref)} → " + ", ".join(f"{svc} ({role})" for svc, role in pairs))

    # One outage line for the whole host set: a wall of never-checked is unreadable
    # when the real news is that the registry could not be reached at all.
    failed = [b for b in items if b.remote_error]
    if failed:
        hosts = printable(", ".join(sorted({ImageRef.parse(b.ref).registry for b in failed})))
        lines.append(
            color(
                f"Registry:  check failed — {hosts} "
                f"({len(failed)} base check(s); their verdicts fall back to the cached checks)",
                Colors.YELLOW,
            )
        )

    dates = [b.checked_at for b in items if b.checked_at]
    if checked and dates:
        lines.append(f"Registry last checked: {printable(max(dates)[:19].replace('T', ' '))}")
    elif not failed:
        # Cached verdicts are on screen above; say how old they are, not just that
        # the registry was not contacted.
        age = f" — newest cached check {max(dates)[:10]}" if dates else ""
        lines.append(f"Registry not checked (--offline){age}.")

    lines.append(color("fresh means the registry digest matches, not that there are no CVEs.", Colors.DIM))
    return lines

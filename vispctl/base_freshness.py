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
import os
import re
import tempfile
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
    """Read the base-lock file; a missing, torn or broken one degrades to a smaller record."""
    path = Path(project_dir) / LOCK_FILE
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return _salvage_truncated(text)
    return data if isinstance(data, dict) else {}


def _salvage_truncated(text: str) -> dict:
    """Read what a truncated JSON object still holds.

    A lock written by an interrupted process ends mid-value. Cutting back to the
    last complete element and closing the containers that were still open costs
    that last entry instead of every record and every cached check in the file —
    whole-file loss would silently pin the host on ``heuristic`` forever. Only a
    read path reaches here: :func:`save_lock` writes atomically.
    """
    stack: list[str] = []
    in_string = escaped = False
    ends: list[tuple[int, int]] = []  # (position, open containers there) after every complete element
    for i, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack:
                break
            stack.pop()
            ends.append((i + 1, len(stack)))
        elif char == ",":
            ends.append((i, len(stack)))  # whatever came before the comma is finished
    for cut, depth in reversed(ends):  # newest complete element first
        closers = "".join("}" if char == "{" else "]" for char in reversed(stack[:depth]))
        try:
            data = json.loads(text[:cut] + closers)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def save_lock(project_dir: Path, lock: dict) -> None:
    """Write the base-lock file (gitignored machine state, not a committed pin).

    Atomic on purpose: ``visp.py images`` caches checks from a read command while
    ``visp.py build`` records a build, and a reader that landed inside a plain
    write would parse nothing, merge nothing, and save the file back without the
    records it never saw.
    """
    path = Path(project_dir) / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")  # 0600, no symlink following
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(lock, indent=1, sort_keys=True))
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise


def _section(lock: dict, name: str) -> dict:
    """One top-level lock section, coerced to a mapping (a hand-edited file may hold anything)."""
    value = lock.get(name)
    if not isinstance(value, dict):
        lock[name] = value = {}
    return value


def record_build(project_dir: Path, built_image_id: str, bases: dict, built_at: str, pull_policy: str) -> dict:
    """Record the bases *built_image_id* consumed. The caller saves the returned lock."""
    lock = load_lock(project_dir)
    _section(lock, "images")[built_image_id] = {
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


_FROM_SPACE_FLAGS = {"--platform"}  # "FROM --platform linux/amd64 x" is a flag plus its own token
_HEREDOC_VERBS = ("RUN", "CMD", "ENTRYPOINT", "SHELL")


def _from_args(parts: list[str]) -> list[str]:
    """The arguments of a FROM line, with both flag spellings removed.

    Only a known flag may swallow the token after it: ``--platform linux/amd64``
    and ``--platform=linux/amd64`` both mean one flag, and an unknown ``--foo``
    must not eat the real ref that follows it.
    """
    args: list[str] = []
    skip = False
    for part in parts[1:]:
        if skip:
            skip = False
        elif part.startswith("--"):
            skip = part in _FROM_SPACE_FLAGS
        else:
            args.append(part)
    return args


def _heredoc_term(tokens: list[str]) -> str:
    """The word that ends a heredoc body: ``<<EOF`` ends at a bare ``EOF``, quotes and a trailing ``+`` aside."""
    for token in tokens:
        if token.startswith("<<"):
            return token.lstrip("<").strip("'\"").rstrip("+") or "EOF"
    return "EOF"


def _parse_stages(text: str) -> list[dict]:
    r"""Parse FROM/COPY lines into ``{name, ref, copies}`` stages, in file order.

    ``ref`` is None for a stage built FROM an earlier stage and for a FROM
    pinned by ``@sha256:``, which cannot move and needs no verdict. ``from_stage``
    keeps the earlier stage's name so the real registry base can be resolved
    through the chain (apache: production <- base <- debian).

    Three things the line-oriented scanner must borrow from the real Dockerfile
    lexer, because these files are third-party (``external/**``): backslash
    continuations are joined before parsing, a heredoc body is data and must not
    be read as instructions, and stage names match case-insensitively.

    A line we cannot read reliably — a ref outside ``[\w.+/@:-]`` (a ``$`` variable,
    control bytes) or a stage name outside ``[\w.-]`` — becomes one literal entry:
    it is reported verbatim (sanitized at display) and never fetched, because a
    guessed ref would be printed as a verdict and sent to a registry.
    """
    lines = re.sub(r"\\\r?\n", " ", text).splitlines()
    stages: list[dict] = []
    defined: set[str] = set()
    i = 0
    while i < len(lines):
        parts = lines[i].strip().split()
        i += 1
        if not parts or parts[0].startswith("#"):
            continue
        verb = parts[0].upper()
        if verb in _HEREDOC_VERBS and any(p.startswith("<<") for p in parts[1:]):
            term = _heredoc_term(parts[1:])
            while i < len(lines) and lines[i].strip() != term:
                i += 1  # a FROM inside the body is a string, not a stage
            continue
        if verb == "FROM":
            args = _from_args(parts)
            if not args:
                continue
            ref = args[0]
            name = args[2].lower() if len(args) >= 3 and args[1].upper() == "AS" else None
            from_stage = None
            if "@sha256:" in ref:
                ref = None
            elif ref.lower() in defined:
                ref, from_stage = None, ref.lower()
            else:
                ref = _normalize_ref(ref)
            if (ref and not _REF_CHARS.fullmatch(ref)) or (name and not _STAGE_NAME.fullmatch(name)):
                ref, name, from_stage = _normalize_ref(" ".join(args)), None, None
            stages.append({"name": name, "ref": ref, "from_stage": from_stage, "copies": []})
            if name:
                defined.add(name)
        elif verb == "COPY" and stages:
            stages[-1]["copies"] += [p.partition("=")[2].lower() for p in parts if p.startswith("--from=")]
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


def _dockerfile_rel(cfg: dict) -> str:
    """The Dockerfile path exactly as ``build.py`` composes its ``-f`` argument."""
    dockerfile = cfg.get("dockerfile", "Dockerfile")
    return dockerfile if dockerfile.startswith("./") else f"{cfg.get('context', '.')}/{dockerfile}"


def _dockerfile_path(project_dir: Path, cfg: dict) -> Path:
    """Where the build definition of *cfg* lives, or where it should."""
    return (Path(project_dir) / _dockerfile_rel(cfg)).resolve()


def stage_bases(project_dir: Path, cfg: dict) -> list[tuple[str, str, str | None]]:
    """Return ``[(ref, role, stage name)]`` of the registry bases of one build config.

    The Dockerfile is located exactly as ``build.py`` composes its ``-f``
    argument. The runtime base is the target stage's (or the last stage's) FROM,
    resolved through internal ``FROM <earlier-stage>`` chains to the registry
    ref at the head; build bases are the bases of the stages the target inherits
    artifacts from, directly or through a stage it inherits FROM.
    """
    path = _dockerfile_path(project_dir, cfg)
    if not path.is_file():
        return []

    stages = _parse_stages(path.read_text(errors="replace"))
    if not stages:
        return []

    target = len(stages) - 1
    if cfg.get("target"):
        target = next((i for i, s in enumerate(stages) if s["name"] == cfg["target"].lower()), target)

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
        data = json.loads(stdout) if rc == 0 and stdout else []
    except (ValueError, RecursionError):
        data = []
    # An unexpected answer — an object, a bare null, a list of strings — is no local state at all.
    entries = [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []

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
    local_id: str | None = None  # the base's id in the local store, when it is there at all

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


def _lock_record(image: object, ref: str) -> dict:
    """One base's record inside the lock, shape-coerced: the lock file is outside input."""
    bases = image.get("bases") if isinstance(image, dict) else None
    entry = bases.get(ref) if isinstance(bases, dict) else None
    return entry if isinstance(entry, dict) else {}


def _digests(value: object) -> frozenset[str]:
    """A digests list from the lock, whatever shape it arrived in."""
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(item) for item in value)


_DATE_PREFIX = re.compile(r"\d{4}-")


def _check_date(value: object) -> str | None:
    """A cached check date, accepted only as a date-shaped string.

    It ends up on a line that says 'as of', so a number or a stray word from a
    hand-edited lock is treated as no date rather than printed as one.
    """
    return value if isinstance(value, str) and _DATE_PREFIX.match(value) else None


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
    if not lock and (project_dir / LOCK_FILE).is_file():
        # Something had to go wrong to get here; say it before printing a wall of heuristic rows
        print(color("base-lock unparsable — starting records fresh", Colors.YELLOW))
    records = lock.get("images") if isinstance(lock.get("images"), dict) else {}
    checks = _section(lock, "checks")
    local_bases, built = local_image_state(runner)

    entries: list[tuple[str, str, str, str | None]] = []
    ids: dict[str, str] = {}
    for service, cfg in configs.items():
        key = f"localhost/{cfg['image']}"
        if key not in built:
            continue  # not built here — nothing to say about it
        ids[service] = built[key]["id"]
        bases = stage_bases(project_dir, cfg)
        if not bases and not _dockerfile_path(project_dir, cfg).is_file():
            # Built, but the definition that names its bases is gone (external/ is
            # gitignored, a Containerfile moved). Say so in the report: a service
            # that silently contributes no row also silently leaves the denominator.
            bases = [(f"no build definition at {_dockerfile_rel(cfg)}", "runtime", None)]
        entries += [(service, ref, role, stage) for ref, role, stage in bases]

    refs = [ref for ref in dict.fromkeys(ref for _, ref, _, _ in entries) if fetchable(ref)]
    remote: dict[str, str] = {}
    errors: dict[str, str] = {}
    if check_remote and refs:
        client = client or RegistryClient()
        with ThreadPoolExecutor(max_workers=4) as pool:
            for ref, digest, error in pool.map(lambda r: _lookup_remote(client, r), refs):
                if error is None and digest is not None:
                    remote[ref] = digest
                else:  # an empty error message is still a failure: never cache it as a resolved ref
                    errors[ref] = error or f"{ref}: registry answered no digest"
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
        record = _lock_record(records.get(ids[service]), ref)
        check = checks.get(ref)
        check = check if isinstance(check, dict) else {}
        cached = check.get("remote_digest")
        if ref in remote:
            digest, source, checked_at = remote[ref], "live", _check_date(checks[ref]["checked_at"])
        elif isinstance(cached, str) and cached:
            digest, source, checked_at = cached, "cache", _check_date(check.get("checked_at"))
        else:
            digest, source, checked_at = None, "none", _check_date(check.get("checked_at"))
        items.append(
            BaseFreshness(
                service=service,
                ref=ref,
                role=role,
                stage_name=stage,
                built_image_id=ids[service],
                recorded_digests=_digests(record.get("digests")),
                local_digests=frozenset((local_bases.get(ref) or {}).get("digests") or ()),
                local_id=(local_bases.get(ref) or {}).get("id"),
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
        elif b.local_id:
            # In the store, but pulled without a manifest digest recorded: there is
            # nothing here to compare the baked-in base against, so say that.
            evidence = f"{b.local_id[:12]} is present locally with no digest recorded (loaded, not pulled)"
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

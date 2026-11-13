"""Operations behind the GUI. No Qt in here, so they can be tested headless.

Wraps the vendored `nup` library and adds what a GUI needs on top of it:

- byte-level progress and cancellation (through a JobContext)
- streaming reads, so extracting or verifying a huge file doesn't load it into RAM
- all-or-nothing packing: a failed or cancelled pack leaves no partial output
- safe extraction: entry names come from an untrusted file and are checked before
  anything is written, because the library's extract_tree joins them to the
  destination path as they are
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
import hashlib
import os
import re
import time
from typing import Protocol

import zstandard

from nup import edit as nup_edit
from nup import format as F
from nup.access import app_meta, iter_children, localized_icons, localized_titles
from nup.discover import ARTIFACT_NAMES, PackInput, discover
from nup.pack import _split_groups, have_zstd, pack
from nup.read import NodeRef, NupReader, PackageInfo
from nup.verify import verify

_CHUNK = 4 << 20
TITLE_ID_RE = re.compile(r"^[0-9a-fA-F]{16}$")
LANG_TAG_RE = re.compile(r"^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$")


class JobCancelled(Exception):
    """The user pressed Cancel."""


class JobError(Exception):
    """An expected failure, with a message fit to show the user."""


class JobContext(Protocol):
    def report(self, done: int, total: int, text: str = "") -> None:
        """Report progress in bytes. Raises JobCancelled if cancel was requested."""


class NullContext:
    """A JobContext for callers that don't track progress."""

    def report(self, done: int, total: int, text: str = "") -> None:
        pass


def format_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def format_share(part: int, whole: int) -> str:
    """Part as a percentage of whole. Never rounds a nonzero part down to 0%."""
    if part and part * 100 < whole:
        return "<1%"
    return f"{part / whole:.0%}"


def display_name(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def describe_version(version: int, display: str = "") -> str:
    """Human version text, with the same reasoning as `nup info`: the display version
    is the truth, and Switch 2 stores a content revision counter in the numeric field
    (one 0x10000 step per update), so don't decode that as major.minor.micro."""
    if display:
        return display
    if version == 0:
        return "none"
    if version & 0xFFFF == 0:
        return f"revision {version >> 16}"
    return f"{version >> 16}.{(version >> 8) & 0xFF}.{version & 0xFF}"


# ---- reading a container ----------------------------------------------------------


@dataclass
class PackageView:
    """Everything the UI shows about one package."""

    info: PackageInfo
    folder: str  # where this package lives in a dump layout, e.g. "updates/458752"
    name: str = ""
    publisher: str = ""
    display_version: str = ""
    source_dump: str = ""
    build_id: str = ""
    has_npdm: bool = False
    has_nacp: bool = False
    icon: bytes | None = None
    titles: dict[str, str] = field(default_factory=dict)
    icons: dict[str, bytes] = field(default_factory=dict)
    file_count: int = 0
    total_size: int = 0

    @property
    def label(self) -> str:
        return f"{self.info.kind_name} {self.info.title_id:016X}"

    @property
    def version_text(self) -> str:
        return describe_version(self.info.version, self.display_version)

    @property
    def standalone(self) -> bool:
        return bool(self.info.flags & F.PKG_FLAG_STANDALONE_UPDATE)


@dataclass
class ContainerView:
    """A snapshot of a container's table of contents.

    The file itself is closed once this exists. Holding it open would lock it on
    Windows and stop metadata edits from replacing it. The reader's parsed TOC lists
    stay usable after close(); only reading file contents needs a fresh reader.
    """

    path: str
    file_size: int
    uuid: str
    reader: NupReader
    packages: list[PackageView]
    dir_totals: list[tuple[int, int]]  # per dir: (file count, total bytes)

    @property
    def file_count(self) -> int:
        return self.reader.file_count

    @property
    def total_size(self) -> int:
        return sum(f[1] for f in self.reader.files)

    def children(self, dir_index: int) -> list[NodeRef]:
        """A directory's children, dirs first, then by name."""
        try:
            kids = iter_children(self.reader, dir_index)
        except IndexError as exc:
            raise F.NupError(f"directory {dir_index} has an out-of-range child") from exc
        return sorted(kids, key=lambda c: (not c.is_dir, c.name.lower()))


def _dir_totals(r: NupReader) -> list[tuple[int, int]]:
    """(file count, bytes) per directory. Producers number directories pre-order, so a
    reverse sweep sees children first. Display-only, so a malformed tree just gets odd
    numbers, and it can't loop forever on a cycle."""
    totals = [(0, 0)] * r.dir_count
    for di in reversed(range(r.dir_count)):
        _, first, count = r.dirs[di]
        files = size = 0
        for cid in r.children[first:first + count]:
            if cid < r.dir_count:
                files += totals[cid][0]
                size += totals[cid][1]
            elif cid - r.dir_count < r.file_count:
                files += 1
                size += r.files[cid - r.dir_count][1]
        totals[di] = (files, size)
    return totals


def _package_folders(packages: list[PackageInfo]) -> list[str]:
    """Each package's folder in the dump layout `pack` expects, so extracting every
    package gives a folder that can be packed again."""
    addon_counts: dict[int, int] = {}
    out = []
    for p in packages:
        if p.kind == F.KIND_BASE:
            out.append("base")
        elif p.kind == F.KIND_UPDATE:
            out.append(f"updates/{p.version}")
        elif p.kind == F.KIND_ADDON:
            index = addon_counts.get(p.title_id, 0)
            addon_counts[p.title_id] = index + 1
            out.append(f"dlc/{p.title_id:016x}/{index}")
        else:
            out.append(f"{p.kind_name}/{p.title_id:016x}-{p.index}")
    return out


def open_container(path: str) -> ContainerView:
    with NupReader(path) as r:
        if not r.packages:
            raise JobError("This container has no packages.")
        totals = _dir_totals(r)
        views = []
        for p, folder in zip(r.packages, _package_folders(r.packages)):
            meta = app_meta(r, p)
            build = meta.get(F.TLV_BUILD_ID, b"")
            files, size = totals[p.root_dir]
            views.append(PackageView(
                info=p,
                folder=folder,
                name=display_name(meta.get(F.TLV_TITLE_NAME, b"")),
                publisher=display_name(meta.get(F.TLV_PUBLISHER, b"")),
                display_version=display_name(meta.get(F.TLV_DISPLAY_VERSION, b"")),
                source_dump=display_name(meta.get(F.TLV_SOURCE_DUMP, b"")),
                build_id=build.rstrip(b"\0").hex().upper(),
                has_npdm=F.TLV_NPDM in meta,
                has_nacp=F.TLV_NACP in meta,
                icon=meta.get(F.TLV_ICON),
                titles={k.decode("ascii", "replace"): display_name(v)
                        for k, v in localized_titles(r, p).items()},
                icons={k.decode("ascii", "replace"): v
                       for k, v in localized_icons(r, p).items()},
                file_count=files,
                total_size=size,
            ))
        uuid = r.super["uuid"].hex()
        return ContainerView(path=path, file_size=r.super["file_size"], uuid=uuid,
                             reader=r, packages=views, dir_totals=totals)


class _ExtentSource:
    """A file-like view of one extent, for zstandard's stream_reader. Slices the mmap
    rather than exporting a memoryview, which would stop the mmap from closing if a
    read fails partway."""

    def __init__(self, r: NupReader, offset: int, size: int) -> None:
        self._r = r
        self._pos = offset
        self._end = offset + size

    def read(self, n: int = -1) -> bytes:
        stop = self._end if n < 0 else min(self._pos + n, self._end)
        data = self._r._mm[self._pos:stop]
        self._pos = stop
        return data


def _iter_file(r: NupReader, file_index: int) -> Iterator[bytes]:
    """Yield a file's decompressed bytes in chunks, enforcing its declared size."""
    name, size, _sha, offset, stored, codec = r.file_info(file_index)
    if size == 0:
        return
    produced = 0
    if codec == F.CODEC_RAW:
        source = _ExtentSource(r, offset, stored)
        while chunk := source.read(_CHUNK):
            produced += len(chunk)
            yield chunk
    elif codec == F.CODEC_ZSTD:
        decompressor = zstandard.ZstdDecompressor()
        try:
            with decompressor.stream_reader(_ExtentSource(r, offset, stored)) as stream:
                while chunk := stream.read(_CHUNK):
                    produced += len(chunk)
                    if produced > size:
                        break  # bound the output: never decompress past the declared size
                    yield chunk
        except zstandard.ZstdError as exc:
            raise F.NupError(f"{display_name(name)}: bad zstd data ({exc})") from exc
    else:
        raise F.NupError(f"{display_name(name)}: unknown codec {codec}")
    if produced != size:
        raise F.NupError(f"{display_name(name)}: expected {size} bytes, "
                         f"decompressed {'more' if produced > size else produced}")


# ---- pack -------------------------------------------------------------------------


@dataclass
class PackageRow:
    """One package `pack` would create, for the preview table."""

    kind: str
    title_id: int
    version: int
    folder: str


def preview_dump(source: str, title_id: str = "") -> list[PackageRow]:
    """What `pack` would find in a dump. Raises JobError if it isn't a usable dump."""
    override = int(title_id, 16) if title_id else None
    try:
        packages = discover(source, override)
    except SystemExit as exc:
        raise JobError(_clean_message(exc)) from None
    except OSError as exc:
        raise JobError(str(exc)) from exc
    return [PackageRow(F.KIND_NAMES[p.kind], p.title_id, p.version,
                       os.path.relpath(p.src, source))
            for p in packages]


def _outputs_for(output: str, packages: list[PackInput], split: bool) -> list[str]:
    if not split:
        return [output]
    return [path for path, _ in _split_groups(output, packages)]


def output_paths(source: str, output: str, split: bool) -> list[str]:
    """Every file a pack will create. Raises JobError if the source isn't a usable dump."""
    try:
        return _outputs_for(output, discover(source, None), split)
    except SystemExit as exc:
        raise JobError(_clean_message(exc)) from None
    except OSError as exc:
        raise JobError(str(exc)) from exc


@dataclass
class PackRequest:
    source: str
    output: str
    level: int = 3
    keep_listings: bool = False
    split: bool = False
    title_id: str = ""
    display_version: str = ""
    publisher: str = ""
    name: str = ""
    icon: str = ""
    titles: dict[str, str] = field(default_factory=dict)  # language -> title
    icons: dict[str, str] = field(default_factory=dict)  # language -> icon path


@dataclass
class PackResult:
    outputs: list[str]
    packages: list[PackageRow]
    input_size: int
    output_size: int
    seconds: float


def _content_size(top: str, keep_listings: bool) -> int:
    """Bytes `pack` will read from one package folder, for the progress total."""
    skip = set() if keep_listings else {n.decode() for n in ARTIFACT_NAMES}
    total = 0
    for folder, _, files in os.walk(top):
        for name in files:
            if name not in skip:
                total += os.path.getsize(os.path.join(folder, name))
    return total


def run_pack(ctx: JobContext, req: PackRequest) -> PackResult:
    # Without zstandard the library falls back to uncompressed output with only a
    # warning on stderr, which a windowed app has nowhere to show.
    if not have_zstd():
        raise JobError("The zstandard module is missing, so packing can't compress.")

    started = time.monotonic()
    override = int(req.title_id, 16) if req.title_id else None
    try:
        packages = discover(req.source, override)
    except SystemExit as exc:
        raise JobError(_clean_message(exc)) from None

    outputs = _outputs_for(req.output, packages, req.split)
    for path in outputs:
        if os.path.exists(path):
            raise JobError(f"{path} already exists. Choose another output name.")

    ctx.report(0, 0, "Scanning source folder")
    total = sum(_content_size(p.src, req.keep_listings) for p in packages)
    done = [0]

    def on_file(_done_files: int, _total_files: int, node: object) -> None:
        done[0] += node.size  # type: ignore[attr-defined]
        ctx.report(done[0], total, display_name(node.name))  # type: ignore[attr-defined]

    names: dict[str | None, str] = dict(req.titles)
    icons: dict[str | None, str] = dict(req.icons)
    if req.name:
        names[None] = req.name
    if req.icon:
        icons[None] = req.icon

    try:
        pack(req.output, req.source, level=req.level, keep_listings=req.keep_listings,
             base_title_id=override, display_version=req.display_version or None,
             publisher=req.publisher or None, names=names, icons=icons, split=req.split,
             progress=on_file)
    except BaseException:
        # Every path was checked above to not exist, so all of these are ours.
        for path in outputs:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise

    rows = [PackageRow(F.KIND_NAMES[p.kind], p.title_id, p.version,
                       os.path.relpath(p.src, req.source)) for p in packages]
    return PackResult(outputs, rows, total, sum(os.path.getsize(p) for p in outputs),
                      time.monotonic() - started)


def _clean_message(exc: SystemExit) -> str:
    """The library reports user errors as SystemExit("pack: ...")."""
    return re.sub(r"^(pack|edit): ", "", str(exc.code))


# ---- extract ----------------------------------------------------------------------


def _safe_component(raw: bytes) -> str:
    """Validate one path component from a container, or raise.

    Rejects anything that could leave the destination folder: separators, drive
    colons, "." and "..", and names Windows would normalize into them (it strips
    trailing dots and spaces, so ".. " becomes "..").
    """
    name = raw.decode("utf-8", "surrogateescape")
    if (not name.rstrip(" .") or any(c in name for c in "/\\:")
            or any(ord(c) < 32 for c in name)):
        raise F.NupError(f"unsafe file name in container: {name!r}")
    return name


@dataclass
class Selection:
    """One thing to extract: a whole package, a directory, or a file."""

    kind: str  # "package" | "dir" | "file"
    index: int


@dataclass
class ExtractPlan:
    dirs: list[str] = field(default_factory=list)
    files: list[tuple[int, str]] = field(default_factory=list)  # (file index, target)
    total: int = 0

    def conflicts(self) -> list[str]:
        return [target for _, target in self.files if os.path.lexists(target)]


def _plan_dir(view: ContainerView, plan: ExtractPlan, dir_index: int, dest: str) -> None:
    # The spec describes a tree, so each directory is reached once. Enforcing that stops
    # a crafted file from looping forever (a cycle) or from expanding exponentially when
    # many slots point at the same directory (a directory bomb), and it bounds the plan
    # by the size of the file's own tables.
    seen = {dir_index}
    stack = [(dir_index, dest)]
    while stack:
        di, path = stack.pop()
        plan.dirs.append(path)
        for child in view.children(di):
            target = os.path.join(path, _safe_component(child.name))
            if child.is_dir:
                if child.index in seen:
                    raise F.NupError(f"directory reached more than once, at {target!r}: "
                                     "the container has a cycle or is a directory bomb")
                seen.add(child.index)
                stack.append((child.index, target))
            else:
                plan.files.append((child.index, target))


def plan_extract(view: ContainerView, selections: list[Selection], dest: str) -> ExtractPlan:
    """Work out exactly what will be written, before writing any of it."""
    plan = ExtractPlan()
    r = view.reader
    for sel in selections:
        if sel.kind == "package":
            pkg = view.packages[sel.index]
            _plan_dir(view, plan, pkg.info.root_dir, os.path.join(dest, *pkg.folder.split("/")))
        elif sel.kind == "dir":
            target = os.path.join(dest, _safe_component(r.dirs[sel.index][0]))
            _plan_dir(view, plan, sel.index, target)
        else:
            plan.dirs.append(dest)
            plan.files.append((sel.index, os.path.join(dest, _safe_component(r.files[sel.index][0]))))
    plan.total = sum(r.files[fi][1] for fi, _ in plan.files)
    return plan


def plan_flat_extract(view: ContainerView, dest: str) -> ExtractPlan:
    """Rescue mode: every file in the file table, ignoring the directory tree."""
    plan = ExtractPlan(dirs=[dest])
    for fi, entry in enumerate(view.reader.files):
        try:
            name = _safe_component(entry[0])
        except F.NupError:
            name = "unnamed"  # it's a rescue: keep the data even if the name is unusable
        plan.files.append((fi, os.path.join(dest, f"{fi:05d}_{name}")))
    plan.total = sum(f[1] for f in view.reader.files)
    return plan


def run_extract(ctx: JobContext, path: str, plan: ExtractPlan) -> int:
    """Write the plan to disk, checking each file's SHA-256. Returns the file count."""
    done = 0
    with NupReader(path) as r:
        for folder in plan.dirs:
            os.makedirs(folder, exist_ok=True)
        for fi, target in plan.files:
            name, _size, sha, *_ = r.file_info(fi)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            digest = hashlib.sha256()
            try:
                with open(target, "wb") as out:
                    for chunk in _iter_file(r, fi):
                        out.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        ctx.report(done, plan.total, display_name(name))
                if digest.digest() != sha:
                    raise F.NupError(f"{display_name(name)}: SHA-256 mismatch, file is corrupt")
            except BaseException:
                try:
                    os.unlink(target)  # don't leave a truncated or corrupt file behind
                except OSError:
                    pass
                raise
    return len(plan.files)


# ---- verify -----------------------------------------------------------------------


@dataclass
class VerifyResult:
    ok: bool
    problems: list[str]
    deep: bool
    files_checked: int = 0


def run_verify(ctx: JobContext, path: str, deep: bool) -> VerifyResult:
    """Structural checks from the library, then a streaming per-file SHA-256 pass.

    Same checks as nup.verify(deep=True), but that one loads each file into RAM and
    can't report progress or be cancelled.
    """
    ctx.report(0, 0, "Checking structure")
    try:
        _, problems = verify(path, deep=False)
    except (IndexError, KeyError) as exc:
        return VerifyResult(False, [f"structure: malformed tree ({exc!r})"], deep)
    if not deep or (problems and problems[0].startswith("open:")):
        return VerifyResult(not problems, problems, deep)

    checked = 0
    with NupReader(path) as r:
        total = sum(f[1] for f in r.files)
        done = 0
        digests: dict[tuple[int, int, int], bytes] = {}  # deduped extents hash only once
        for fi, (name, size, sha, offset, stored, codec) in enumerate(r.files):
            label = f"file {fi} ({display_name(name)})"
            if codec == F.CODEC_RAW and stored != size:
                problems.append(f"{label}: raw extent size {stored} != {size}")
            key = (offset, stored, codec)
            digest = digests.get(key) if size else None
            try:
                if digest is None:
                    hasher = hashlib.sha256()
                    read = 0
                    for chunk in _iter_file(r, fi):
                        hasher.update(chunk)
                        read += len(chunk)
                        ctx.report(done + read, total, display_name(name))
                    digest = hasher.digest()
                    digests[key] = digest
            except F.NupError as exc:
                problems.append(f"{label}: read failed: {exc}")
            else:
                if digest != sha:
                    problems.append(f"{label}: SHA-256 mismatch")
            done += size
            checked += 1
            ctx.report(done, total, display_name(name))
    return VerifyResult(not problems, problems, deep, checked)


# ---- edit -------------------------------------------------------------------------


@dataclass
class EditRequest:
    """Only what changed. None means leave as is; the clear_* flags remove a value."""

    package_index: int
    name: str | None = None
    publisher: str | None = None
    display_version: str | None = None
    icon: str | None = None
    clear_name: bool = False
    clear_publisher: bool = False
    clear_display_version: bool = False
    clear_icon: bool = False
    lang_names: dict[str, str] = field(default_factory=dict)  # "" removes the language
    lang_icons: dict[str, str] = field(default_factory=dict)  # "" removes the language

    def has_changes(self) -> bool:
        return any((self.name is not None, self.publisher is not None,
                    self.display_version is not None, self.icon is not None,
                    self.clear_name, self.clear_publisher, self.clear_display_version,
                    self.clear_icon, self.lang_names, self.lang_icons))


def run_edit(ctx: JobContext, path: str, req: EditRequest) -> None:
    """Rewrite a package's metadata. The library copies the container to a temp file
    and swaps it in atomically, so this needs no open reader on `path` and a failure
    leaves the original untouched."""
    ctx.report(0, 0, "Rewriting metadata")
    nup_edit.edit(path, package=str(req.package_index), name=req.name,
                  publisher=req.publisher, display_version=req.display_version,
                  icon=req.icon, lang_names=req.lang_names, lang_icons=req.lang_icons,
                  clear_name=req.clear_name, clear_publisher=req.clear_publisher,
                  clear_display_version=req.clear_display_version,
                  clear_icon=req.clear_icon)

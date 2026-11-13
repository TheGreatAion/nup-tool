"""The non-GUI operations, end to end against real files on disk."""

from __future__ import annotations

import os
from pathlib import Path

from conftest import BASE_ID, BUILD_ID, DLC_ID, patch_toc, tree_contents
import pytest
import zstandard

from nup import format as F
from nup.meta import parse_block
from nup.read import NupReader
from nup_gui import core


def pack_it(source: Path, output: Path, **kw) -> core.PackResult:
    return core.run_pack(core.NullContext(), core.PackRequest(str(source), str(output), **kw))


class CancelAfter:
    def __init__(self, reports: int) -> None:
        self.left = reports

    def report(self, done: int, total: int, text: str = "") -> None:
        self.left -= 1
        if self.left < 0:
            raise core.JobCancelled


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def report(self, done: int, total: int, text: str = "") -> None:
        self.calls.append((done, total))


# ---- pack and read back ----


def test_pack_finds_every_package_and_metadata(dump: Path, tmp_path: Path, icon: Path) -> None:
    out = tmp_path / "game.nup"
    result = pack_it(dump, out, name="Test Game", publisher="Acme", display_version="1.2.3",
                     icon=str(icon), titles={"ja": "テストゲーム"}, icons={"ja": str(icon)})
    assert result.outputs == [str(out)]
    assert [p.kind for p in result.packages] == ["base", "update", "addon"]

    view = core.open_container(str(out))
    base, update, addon = view.packages
    assert (base.info.kind_name, base.info.title_id) == ("base", BASE_ID)
    assert base.name == "Test Game"
    assert base.publisher == "Acme"
    assert base.version_text == "1.2.3"
    assert base.icon == icon.read_bytes()
    assert base.titles == {"ja": "テストゲーム"}
    assert base.icons == {"ja": icon.read_bytes()}
    assert base.build_id == BUILD_ID.hex().upper()
    assert base.has_npdm
    assert update.info.version == 458752
    assert update.version_text == "revision 7"
    assert addon.info.title_id == int(DLC_ID, 16)
    assert update.name == ""  # app metadata only rides on the base


def test_pack_compresses_and_skips_listing_by_default(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    result = pack_it(dump, out)
    assert result.output_size < result.input_size
    view = core.open_container(str(out))
    names = [f[0] for f in view.reader.files]
    assert b".listing" not in names
    codecs = {f[0]: f[5] for f in view.reader.files}
    assert codecs[b"level.bin"] == F.CODEC_ZSTD
    assert codecs[b"random.bin"] == F.CODEC_RAW  # incompressible, stored raw


def test_keep_listings(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out, keep_listings=True)
    assert b".listing" in [f[0] for f in core.open_container(str(out)).reader.files]


def test_split_writes_one_file_per_group(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    assert core.output_paths(str(dump), str(out), True) == [
        str(out), str(tmp_path / "game.update.458752.nup"), str(tmp_path / "game.dlc.nup")]
    result = pack_it(dump, out, split=True)
    assert len(result.outputs) == 3
    for path in result.outputs:
        assert core.run_verify(core.NullContext(), path, deep=True).ok
    assert core.open_container(result.outputs[1]).packages[0].standalone


def test_title_id_override(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out, title_id="0100000000000123")
    assert core.open_container(str(out)).packages[0].info.title_id == 0x0100000000000123


def test_progress_covers_all_bytes(dump: Path, tmp_path: Path) -> None:
    ctx = Recorder()
    result = core.run_pack(ctx, core.PackRequest(str(dump), str(tmp_path / "game.nup")))
    assert ctx.calls[-1] == (result.input_size, result.input_size)
    assert [c[0] for c in ctx.calls] == sorted(c[0] for c in ctx.calls)


# ---- pack failure handling ----


def test_cancelled_pack_leaves_no_files(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    with pytest.raises(core.JobCancelled):
        core.run_pack(CancelAfter(3), core.PackRequest(str(dump), str(out)))
    assert not out.exists()


def test_cancelled_split_pack_removes_finished_containers(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    # the base has 7 files, so the base container is complete by report 9
    with pytest.raises(core.JobCancelled):
        core.run_pack(CancelAfter(9), core.PackRequest(str(dump), str(out), split=True))
    assert [p.name for p in tmp_path.glob("*.nup")] == []


def test_failed_pack_leaves_no_files(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    with pytest.raises(OSError):
        pack_it(dump, out, icon=str(tmp_path / "missing.png"))
    assert not out.exists()


def test_existing_output_is_refused_and_untouched(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    out.write_bytes(b"precious")
    with pytest.raises(core.JobError, match="already exists"):
        pack_it(dump, out)
    assert out.read_bytes() == b"precious"


def test_existing_split_output_is_refused_before_touching_anything(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    other = tmp_path / "game.dlc.nup"
    other.write_bytes(b"precious")
    with pytest.raises(core.JobError, match="already exists"):
        pack_it(dump, out, split=True)
    assert other.read_bytes() == b"precious"
    assert not out.exists()


def test_preview_rejects_a_folder_that_is_not_a_dump(tmp_path: Path) -> None:
    (tmp_path / "holiday photos").mkdir()
    with pytest.raises(core.JobError, match="neither a dump bundle"):
        core.preview_dump(str(tmp_path / "holiday photos"))


def test_preview_rejects_a_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(core.JobError):
        core.preview_dump(str(tmp_path / "nope"))


# ---- spec conformance of the vendored fix ----


def test_streamed_files_embed_content_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SPEC: a zstd frame must embed the content size. Upstream omitted it above 256 MiB."""
    monkeypatch.setattr("nup.pack.STREAM_THRESHOLD", 1024)
    (tmp_path / "d/romfs").mkdir(parents=True)
    (tmp_path / "d/romfs/big.bin").write_bytes(b"compressible " * 100_000)
    out = tmp_path / "o.nup"
    pack_it(tmp_path / "d", out)
    with NupReader(str(out)) as r:
        _name, size, _sha, offset, _stored, codec = r.files[0]
        assert codec == F.CODEC_ZSTD
        assert zstandard.get_frame_parameters(r._mm[offset:offset + 64]).content_size == size
    assert core.run_verify(core.NullContext(), str(out), deep=True).ok


# ---- extract ----


def test_extract_everything_round_trips(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out)
    view = core.open_container(str(out))
    dest = tmp_path / "x" / "game"
    selections = [core.Selection("package", p.info.index) for p in view.packages]
    plan = core.plan_extract(view, selections, str(dest))
    assert not plan.conflicts()
    assert core.run_extract(core.NullContext(), str(out), plan) == len(plan.files)

    expected = tree_contents(dump)
    del expected["base/exefs/.listing"]
    assert tree_contents(dest) == expected

    # What came out can be packed again, and gives the identical container.
    again = tmp_path / "again.nup"
    pack_it(dest, again)
    assert again.read_bytes() == out.read_bytes()


def test_extract_single_file_and_folder(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out)
    view = core.open_container(str(out))
    romfs = view.packages[0].info.romfs_dir
    kids = {c.name: c for c in view.children(romfs)}
    dest = tmp_path / "x"

    selections = [core.Selection("file", kids[b"random.bin"].index),
                  core.Selection("dir", kids[b"data"].index)]
    core.run_extract(core.NullContext(), str(out), core.plan_extract(view, selections, str(dest)))
    assert sorted(tree_contents(dest)) == ["data/level.bin", "data/shared.bin", "random.bin"]


def test_plan_reports_conflicts(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out)
    view = core.open_container(str(out))
    dest = tmp_path / "x"
    plan = core.plan_extract(view, [core.Selection("package", 2)], str(dest))
    core.run_extract(core.NullContext(), str(out), plan)
    assert len(core.plan_extract(view, [core.Selection("package", 2)], str(dest)).conflicts()) == 1


def test_flat_rescue_extract(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out)
    view = core.open_container(str(out))
    dest = tmp_path / "flat"
    plan = core.plan_flat_extract(view, str(dest))
    assert core.run_extract(core.NullContext(), str(out), plan) == view.file_count
    assert len(os.listdir(dest)) == view.file_count


def test_corrupt_file_is_not_left_behind_after_extract(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out)
    target = corrupt_file(out, b"random.bin")
    view = core.open_container(str(out))
    dest = tmp_path / "x"
    plan = core.plan_flat_extract(view, str(dest))
    with pytest.raises(F.NupError, match="SHA-256"):
        core.run_extract(core.NullContext(), str(out), plan)
    assert not (dest / f"{target:05d}_random.bin").exists()


def test_cancelled_extract_leaves_no_partial_file(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out)
    view = core.open_container(str(out))
    plan = core.plan_flat_extract(view, str(tmp_path / "x"))
    with pytest.raises(core.JobCancelled):
        core.run_extract(CancelAfter(0), str(out), plan)
    assert list((tmp_path / "x").iterdir()) == []


# ---- hostile containers ----


@pytest.mark.parametrize("name", [b"..", b".", b".. ", b"...", b"  ", b"", b"a/b", b"a\\b",
                                  b"C:evil", b"file.txt:stream", b"a\x00b", b"a\nb"])
def test_unsafe_names_are_rejected(name: bytes) -> None:
    with pytest.raises(F.NupError):
        core._safe_component(name)


@pytest.mark.parametrize("name", [b"normal.bin", b".hidden", "日本語.txt".encode(), b"a b", b"a..b"])
def test_ordinary_names_are_allowed(name: bytes) -> None:
    assert core._safe_component(name)


def rename_in_pool(path: Path, old: bytes, new: bytes) -> None:
    assert len(old) == len(new)

    def change(toc: bytearray) -> None:
        at = toc.find(old)
        assert at >= 0
        toc[at:at + len(old)] = new

    patch_toc(path, change)


def test_path_traversal_container_is_refused(tmp_path: Path) -> None:
    (tmp_path / "d/romfs").mkdir(parents=True)
    (tmp_path / "d/romfs/XXXXXXXX").write_bytes(b"payload")
    out = tmp_path / "evil.nup"
    pack_it(tmp_path / "d", out)
    rename_in_pool(out, b"XXXXXXXX", b"../pwned")

    view = core.open_container(str(out))  # opening and browsing it is fine
    dest = tmp_path / "out" / "inner"
    with pytest.raises(F.NupError, match="unsafe"):
        core.plan_extract(view, [core.Selection("package", 0)], str(dest))
    assert not (tmp_path / "out").exists()

    # Rescue mode keeps the data under a safe name.
    plan = core.plan_flat_extract(view, str(dest))
    core.run_extract(core.NullContext(), str(out), plan)
    assert [p.name for p in dest.iterdir()] == ["00000_unnamed"]
    assert not (tmp_path / "out" / "pwned").exists()


def test_directory_cycle_is_refused_without_hanging(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out)
    with NupReader(str(out)) as r:
        romfs = r.packages[0].romfs_dir
        _, first, count = r.dirs[romfs]
        data_slot = next(s for s in range(first, first + count)
                         if r.children[s] < r.dir_count)
        children_offset = (F._toc_head_s.size + len(r.packages) * F.PKG_SIZE
                           + r.dir_count * F.DIR_SIZE + r.file_count * F.FILE_SIZE)

    def change(toc: bytearray) -> None:
        toc[children_offset + data_slot * 4:children_offset + data_slot * 4 + 4] = \
            romfs.to_bytes(4, "little")  # romfs/data now points back at romfs

    patch_toc(out, change)
    view = core.open_container(str(out))
    with pytest.raises(F.NupError, match="cycle"):
        core.plan_extract(view, [core.Selection("package", 0)], str(tmp_path / "x"))
    assert not core.run_verify(core.NullContext(), str(out), deep=False).ok


def test_directory_bomb_is_refused(dump: Path, tmp_path: Path) -> None:
    """Upstream issue #27: slots sharing a directory make extraction expand exponentially."""
    out = tmp_path / "game.nup"
    pack_it(dump, out)
    with NupReader(str(out)) as r:
        pkg = r.packages[0]
        _, first, count = r.dirs[pkg.root_dir]
        slots = [s for s in range(first, first + count) if r.children[s] < r.dir_count]
        assert len(slots) == 2  # exefs and romfs
        children_offset = (F._toc_head_s.size + len(r.packages) * F.PKG_SIZE
                           + r.dir_count * F.DIR_SIZE + r.file_count * F.FILE_SIZE)
        romfs = pkg.romfs_dir

    def change(toc: bytearray) -> None:
        for slot in slots:  # both root children now lead to the same directory
            at = children_offset + slot * 4
            toc[at:at + 4] = romfs.to_bytes(4, "little")

    patch_toc(out, change)
    view = core.open_container(str(out))
    with pytest.raises(F.NupError, match="more than once"):
        core.plan_extract(view, [core.Selection("package", 0)], str(tmp_path / "x"))
    assert not (tmp_path / "x").exists()


def test_metadata_ids_match_the_spec(dump: Path, tmp_path: Path, icon: Path) -> None:
    """SPEC section 5 numbers TLVs from 0x0001. Upstream's enum started at 0 (PRs #22, #28)."""
    assert {t.name: int(t) for t in F.TVL} == {
        "NPDM": 1, "NACP": 2, "ICON": 3, "DISPLAY_VERSION": 4, "TITLE_NAME": 5, "PUBLISHER": 6,
        "BUILD_ID": 7, "SOURCE_DUMP": 8, "LOCALIZED_TITLES": 9, "LOCALIZED_ICONS": 10}

    out = tmp_path / "game.nup"
    pack_it(dump, out, name="Title", publisher="Pub", display_version="1.0", icon=str(icon),
            titles={"ja": "題"})
    with NupReader(str(out)) as r:
        pkg = r.packages[0]
        block = parse_block(r._mm[pkg.meta_offset:pkg.meta_offset + pkg.meta_size])
    assert block[0x0005] == b"Title"
    assert block[0x0006] == b"Pub"
    assert block[0x0004] == b"1.0"
    assert block[0x0003] == icon.read_bytes()
    assert block[0x0008] == b"game"
    assert block[0x0007] == BUILD_ID
    assert block[0x0001][:4] == b"META"


def test_not_a_nup_file_gives_a_clean_error(tmp_path: Path) -> None:
    junk = tmp_path / "junk.nup"
    junk.write_bytes(b"this is not a container" * 50)
    with pytest.raises(F.NupError, match="bad magic"):
        core.open_container(str(junk))
    os.unlink(junk)  # the failed open must not keep the file locked on Windows


def test_empty_file_gives_a_clean_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty.nup"
    empty.write_bytes(b"")
    with pytest.raises(F.NupError):
        core.open_container(str(empty))


# ---- verify ----


def corrupt_file(path: Path, name: bytes) -> int:
    """Flip a byte inside one file's extent. Returns its file index."""
    with NupReader(str(path)) as r:
        index = next(i for i, f in enumerate(r.files) if f[0] == name)
        offset = r.files[index][3]
    data = bytearray(path.read_bytes())
    data[offset] ^= 0xFF
    path.write_bytes(data)
    return index


def test_verify_passes_a_good_file(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out)
    ctx = Recorder()
    result = core.run_verify(ctx, str(out), deep=True)
    assert result.ok and not result.problems
    assert result.files_checked == core.open_container(str(out)).file_count
    assert ctx.calls[-1][0] == ctx.calls[-1][1] > 0


@pytest.mark.parametrize("name", [b"random.bin", b"level.bin"])  # raw and zstd
def test_verify_catches_corruption(dump: Path, tmp_path: Path, name: bytes) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out)
    corrupt_file(out, name)
    assert core.run_verify(core.NullContext(), str(out), deep=False).ok  # structure is intact
    result = core.run_verify(core.NullContext(), str(out), deep=True)
    assert not result.ok
    assert name.decode() in result.problems[0]


def test_verify_agrees_with_the_library(dump: Path, tmp_path: Path) -> None:
    from nup.verify import verify
    out = tmp_path / "game.nup"
    pack_it(dump, out)
    assert verify(str(out), deep=True)[0] is core.run_verify(core.NullContext(), str(out), True).ok
    corrupt_file(out, b"random.bin")
    assert verify(str(out), deep=True)[0] is core.run_verify(core.NullContext(), str(out), True).ok


def test_verify_reports_a_truncated_file(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out)
    out.write_bytes(out.read_bytes()[:-100])
    result = core.run_verify(core.NullContext(), str(out), deep=True)
    assert not result.ok and "open:" in result.problems[0]


def test_verify_can_be_cancelled(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out)
    with pytest.raises(core.JobCancelled):
        core.run_verify(CancelAfter(2), str(out), deep=True)
    os.unlink(out)  # and it released the file


# ---- edit ----


def test_edit_changes_only_what_was_asked(dump: Path, tmp_path: Path, icon: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out, name="Old", publisher="Keep Me", display_version="1.0",
            titles={"ja": "古い", "fr": "Vieux"})
    before = core.open_container(str(out))

    core.run_edit(core.NullContext(), str(out), core.EditRequest(
        package_index=0, name="New", clear_display_version=True, icon=str(icon),
        lang_names={"ja": "新しい", "fr": ""}))

    after = core.open_container(str(out))
    base = after.packages[0]
    assert base.name == "New"
    assert base.publisher == "Keep Me"
    assert base.display_version == ""
    assert base.icon == icon.read_bytes()
    assert base.titles == {"ja": "新しい"}
    assert base.build_id == before.packages[0].build_id
    assert after.reader.files == before.reader.files
    assert after.uuid == before.uuid
    assert core.run_verify(core.NullContext(), str(out), deep=True).ok
    assert not os.path.exists(str(out) + ".tmp")


def test_edit_targets_the_chosen_package(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out, name="Base Name")
    core.run_edit(core.NullContext(), str(out), core.EditRequest(package_index=1, name="Patch"))
    view = core.open_container(str(out))
    assert [p.name for p in view.packages] == ["Base Name", "Patch", ""]


def test_edit_failure_keeps_the_original(dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "game.nup"
    pack_it(dump, out, name="Original")
    before = out.read_bytes()
    with pytest.raises(OSError):
        core.run_edit(core.NullContext(), str(out), core.EditRequest(
            package_index=0, icon=str(tmp_path / "missing.png")))
    assert out.read_bytes() == before
    assert not os.path.exists(str(out) + ".tmp")


# ---- formatting ----


@pytest.mark.parametrize("size,text", [(0, "0 B"), (1023, "1023 B"), (1024, "1.0 KB"),
                                       (5 * 1024 ** 3, "5.0 GB"), (3 * 1024 ** 4, "3.0 TB")])
def test_format_size(size: int, text: str) -> None:
    assert core.format_size(size) == text


@pytest.mark.parametrize("version,display,text", [
    (0, "", "none"), (458752, "", "revision 7"), (0x10203, "", "1.2.3"), (458752, "2.0", "2.0")])
def test_describe_version(version: int, display: str, text: str) -> None:
    assert core.describe_version(version, display) == text

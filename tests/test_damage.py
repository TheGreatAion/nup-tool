"""Spotting programs that came out short, as they do after an FTP text-mode copy."""

from __future__ import annotations

from pathlib import Path

from conftest import BASE_ID, npdm, nso_with_text, text_mode_copy, write

from nup_gui import core

TEXT = b"code\r\nmore code\r\n" * 100  # 200 CR LF pairs


def test_complete_programs_pass(tmp_path: Path) -> None:
    for name, data in (("plain", nso_with_text(TEXT)), ("lz4", nso_with_text(TEXT, compressed=True))):
        write(tmp_path / name, data)
        assert core.nso_missing_bytes(str(tmp_path / name)) == 0


def test_text_mode_copy_is_caught(tmp_path: Path) -> None:
    path = tmp_path / "main"
    write(path, text_mode_copy(nso_with_text(TEXT)))
    assert core.nso_missing_bytes(str(path)) == 200
    write(path, text_mode_copy(nso_with_text(TEXT, compressed=True)))
    assert core.nso_missing_bytes(str(path)) == 200


def test_other_files_are_ignored(tmp_path: Path) -> None:
    write(tmp_path / "main.npdm", npdm(BASE_ID))
    write(tmp_path / "tiny", b"NSO0")
    assert core.nso_missing_bytes(str(tmp_path / "main.npdm")) == 0
    assert core.nso_missing_bytes(str(tmp_path / "missing")) == 0
    assert core.nso_missing_bytes(str(tmp_path / "tiny")) == 0x100 - 4


def test_short_programs_lists_each_damaged_program(dump: Path) -> None:
    rows = core.preview_dump(str(dump))
    folders = [r.folder for r in rows]
    assert core.short_programs(str(dump), folders) == []
    write(dump / "base/exefs/main", text_mode_copy(nso_with_text(TEXT)))
    write(dump / "updates/458752/exefs/sdk", text_mode_copy(nso_with_text(TEXT * 2)))
    found = core.short_programs(str(dump), folders)
    assert [(Path(p).as_posix(), n) for p, n in found] == \
        [("base/exefs/main", 200), ("updates/458752/exefs/sdk", 400)]


def test_single_folder_dump(tmp_path: Path) -> None:
    write(tmp_path / "exefs/main", text_mode_copy(nso_with_text(TEXT)))
    write(tmp_path / "romfs/data.bin", b"data")
    rows = core.preview_dump(str(tmp_path))
    assert [n for _, n in core.short_programs(str(tmp_path), [r.folder for r in rows])] == [200]

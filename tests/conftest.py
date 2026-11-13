"""Builds fake dumps. Nothing here is real game data: the NPDM and NSO are just enough
bytes for the library to find a program ID and build ID."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import struct
import sys
import zlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from nup import format as F

BASE_ID = 0x0100AAAABBBB0000
DLC_ID = "0100aaaabbbb1001"
BUILD_ID = bytes(range(0x20))


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body)))

    rows = (bytes(1) + bytes(rgb) * width) * height
    return (bytes.fromhex("89504e470d0a1a0a")
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b""))


PNG = _png(64, 64, (220, 60, 60))


def npdm(program_id: int) -> bytes:
    return b"META" + bytes(0x7C) + b"ACI0" + bytes(0xC) + struct.pack("<Q", program_id) + bytes(8)


def nso() -> bytes:
    return b"NSO0" + bytes(0x3C) + BUILD_ID + bytes(0x20)


PNG_BLUE = _png(64, 64, (40, 80, 220))


def nacp(titles: dict[int, tuple[str, str]], display_version: str = "") -> bytes:
    """A control.nacp: {language index: (name, publisher)} and a display version."""
    data = bytearray(0x4000)
    for index, (name, publisher) in titles.items():
        data[index * 0x300:index * 0x300 + len(name.encode())] = name.encode()
        start = index * 0x300 + 0x200
        data[start:start + len(publisher.encode())] = publisher.encode()
    data[0x3060:0x3060 + len(display_version)] = display_version.encode()
    return bytes(data)


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def make_dump(root: Path) -> Path:
    """A full dump: base, one update, one DLC."""
    write(root / "base/exefs/main.npdm", npdm(BASE_ID))
    write(root / "base/exefs/main", nso())
    write(root / "base/exefs/.listing", b"dump tool artifact")
    write(root / "base/romfs/data/level.bin", b"level data " * 5000)
    write(root / "base/romfs/data/shared.bin", b"same in two places " * 500)
    write(root / "base/romfs/copy_of_shared.bin", b"same in two places " * 500)
    write(root / "base/romfs/random.bin", b"".join(hashlib.sha256(bytes([i])).digest() for i in range(64)))
    write(root / "base/romfs/empty.txt", b"")
    write(root / "base/romfs/日本語.txt", "こんにちは".encode())
    write(root / "updates/458752/exefs/main.npdm", npdm(BASE_ID + 0x800))
    write(root / "updates/458752/exefs/main", nso())
    write(root / "updates/458752/romfs/patch.bin", b"patch " * 2000)
    write(root / f"dlc/{DLC_ID}/0/romfs/extra.bin", b"dlc content " * 3000)
    return root


def tree_contents(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def patch_toc(path: Path, change) -> None:
    """Edit a container's TOC and fix up its hash, as a malicious producer could."""
    data = bytearray(path.read_bytes())
    sup = F.parse_super(bytes(data[:F.HEADER_SIZE]))
    start, size = sup["toc_offset"], sup["toc_size"]
    toc = bytearray(data[start:start + size])
    change(toc)
    data[start:start + size] = toc
    data[:F.HEADER_SIZE] = F.pack_super(
        F.MAGIC, sup["header_size"], sup["header_version"], start, size,
        hashlib.sha256(toc).digest(), sup["blob_offset"], sup["file_size"],
        sup["package_count"], sup["flags"], sup["uuid"])
    path.write_bytes(data)


@pytest.fixture
def dump(tmp_path: Path) -> Path:
    return make_dump(tmp_path / "game")


@pytest.fixture
def icon(tmp_path: Path) -> Path:
    path = tmp_path / "icon.png"
    path.write_bytes(PNG)
    return path

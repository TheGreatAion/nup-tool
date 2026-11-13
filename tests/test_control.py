"""Reading metadata from a game's control data (control.nacp and its icons)."""

from __future__ import annotations

from pathlib import Path

from conftest import PNG, PNG_BLUE, nacp, write
import pytest

from nup_gui import core

EN, JA, FR = 0, 2, 3


def test_reads_titles_publisher_version_and_icons(tmp_path: Path) -> None:
    folder = tmp_path / "control"
    write(folder / "control.nacp", nacp({EN: ("Test Game", "Acme"), FR: ("Test Game", "Acme"),
                                         JA: ("テストゲーム", "アクメ")}, "1.2.3"))
    write(folder / "icon_AmericanEnglish.dat", PNG)
    write(folder / "icon_French.dat", PNG)
    write(folder / "icon_Japanese.dat", PNG_BLUE)

    data = core.read_control(str(folder / "control.nacp"))
    assert (data.name, data.publisher, data.display_version) == ("Test Game", "Acme", "1.2.3")
    assert data.icon == str(folder / "icon_AmericanEnglish.dat")
    # Only what differs from the defaults: readers fall back to those.
    assert data.titles == {"ja": "テストゲーム"}
    assert data.icons == {"ja": str(folder / "icon_Japanese.dat")}


def test_defaults_come_from_the_first_language_present(tmp_path: Path) -> None:
    path = tmp_path / "control.nacp"
    write(path, nacp({JA: ("テストゲーム", "")}, "2.0"))
    write(tmp_path / "icon_Japanese.dat", PNG)
    data = core.read_control(str(path))
    assert (data.name, data.publisher, data.titles) == ("テストゲーム", "", {})
    assert data.icon == str(tmp_path / "icon_Japanese.dat") and data.icons == {}


def test_icons_are_optional(tmp_path: Path) -> None:
    path = tmp_path / "control.nacp"
    write(path, nacp({EN: ("Game", "Pub")}))
    data = core.read_control(str(path))
    assert data.icon == "" and data.icons == {} and data.display_version == ""


@pytest.mark.parametrize("content, message", [
    (b"short", "isn't control data"),
    (bytes(0x4000), "no titles or version"),
])
def test_rejects_files_that_are_not_control_data(tmp_path: Path, content: bytes, message: str) -> None:
    path = tmp_path / "control.nacp"
    path.write_bytes(content)
    with pytest.raises(core.JobError, match=message):
        core.read_control(str(path))


def test_missing_file_is_a_job_error(tmp_path: Path) -> None:
    with pytest.raises(core.JobError, match="Couldn't read"):
        core.read_control(str(tmp_path / "control.nacp"))


def test_find_prefers_the_newest_update_then_base_then_the_folder(dump: Path) -> None:
    assert core.find_control(str(dump)) is None
    write(dump / "control.nacp", nacp({EN: ("Root", "")}))
    assert core.find_control(str(dump)) == str(dump / "control.nacp")
    write(dump / "base/control/control.nacp", nacp({EN: ("Base", "")}))
    assert core.find_control(str(dump)) == str(dump / "base/control/control.nacp")
    write(dump / "updates/65536/control/romfs/control.nacp", nacp({EN: ("Old", "")}))
    write(dump / "updates/458752/CONTROL.NACP", nacp({EN: ("New", "")}))
    found = core.find_control(str(dump))
    assert found is not None and Path(found).parent == dump / "updates/458752"
    assert core.read_control(found).name == "New"


def test_find_handles_a_missing_folder(tmp_path: Path) -> None:
    assert core.find_control(str(tmp_path / "nowhere")) is None

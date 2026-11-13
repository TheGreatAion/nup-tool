"""Drives the real window with Qt's offscreen platform. Modal dialogs are stubbed out
and recorded; everything else, including the worker threads, runs for real."""

from __future__ import annotations

import os
from pathlib import Path

from conftest import PNG, tree_contents
import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from nup_gui import core
from nup_gui.__main__ import configure
from nup_gui.edit_dialog import EditDialog
from nup_gui.main_window import MainWindow


@pytest.fixture(scope="session")
def app() -> QApplication:
    application = QApplication.instance() or QApplication([])
    configure(application)
    return application


class Dialogs:
    """Records the modal dialogs that would have appeared."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.errors: list[str] = []
        self.boxes: list[str] = []
        self.questions: list[str] = []
        self.answer = QMessageBox.StandardButton.Yes
        self.directory = ""

        def box_exec(box: QMessageBox) -> int:
            self.boxes.append(f"{box.text()} | {box.informativeText()}")
            return 0

        monkeypatch.setattr(QMessageBox, "exec", box_exec)
        monkeypatch.setattr(QMessageBox, "critical",
                            staticmethod(lambda _p, _t, text: self.errors.append(text)))

        def question(_parent, _title, text):
            self.questions.append(text)
            return self.answer

        monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            staticmethod(lambda *_a, **_k: self.directory))


@pytest.fixture
def dialogs(monkeypatch: pytest.MonkeyPatch) -> Dialogs:
    return Dialogs(monkeypatch)


@pytest.fixture
def window(app: QApplication, dialogs: Dialogs):
    win = MainWindow()
    win.show()
    yield win
    wait_idle(app, win)
    win.close()


def wait_idle(app: QApplication, win: MainWindow, seconds: float = 30) -> None:
    import time
    deadline = time.monotonic() + seconds
    while win.jobs.busy:
        assert time.monotonic() < deadline, "job didn't finish"
        app.processEvents()
    app.processEvents()


def pack_through_ui(app: QApplication, win: MainWindow, dump: Path) -> Path:
    win.pack_page.set_source(str(dump))
    assert win.pack_page.pack_button.isEnabled(), win.pack_page.message.text()
    win.pack_page.pack_button.click()
    wait_idle(app, win)
    return Path(win.pack_page.output.text())


def open_through_ui(app: QApplication, win: MainWindow, path: Path) -> core.ContainerView:
    win.open_container(str(path))
    wait_idle(app, win)
    assert win.browse_page.view is not None
    return win.browse_page.view


def find_child(parent, name: str):
    for i in range(parent.childCount()):
        if parent.child(i).text(0) == name:
            return parent.child(i)
    raise AssertionError(f"{name} not in {[parent.child(i).text(0) for i in range(parent.childCount())]}")


# ---- pack page ----


def test_starts_with_pack_disabled_and_no_complaints(window: MainWindow) -> None:
    assert not window.pack_page.pack_button.isEnabled()
    assert window.pack_page.message.text() == ""
    assert not window.jobs.isVisible()


def test_choosing_a_dump_previews_it_and_suggests_an_output(window: MainWindow, dump: Path) -> None:
    window.pack_page.set_source(str(dump))
    page = window.pack_page
    assert page.table.rowCount() == 3
    assert [page.table.item(i, 0).text() for i in range(3)] == ["base", "update", "addon"]
    assert page.table.item(0, 1).text() == "0100AAAABBBB0000"
    assert page.output.text() == str(dump.parent / "game.nup")
    assert page.pack_button.isEnabled()


def test_suggested_output_doesnt_replace_a_typed_one(window: MainWindow, dump: Path, tmp_path: Path) -> None:
    page = window.pack_page
    page.output.edit.setText(str(tmp_path / "mine.nup"))
    page.output.edit.textEdited.emit("")  # as if the user typed it
    page.set_source(str(dump))
    assert page.output.text() == str(tmp_path / "mine.nup")


def test_not_a_dump_is_explained(window: MainWindow, tmp_path: Path) -> None:
    (tmp_path / "photos").mkdir()
    window.pack_page.set_source(str(tmp_path / "photos"))
    assert "neither a dump bundle" in window.pack_page.message.text()
    assert not window.pack_page.pack_button.isEnabled()


def test_bad_title_id_blocks_pack(window: MainWindow, dump: Path) -> None:
    page = window.pack_page
    page.set_source(str(dump))
    page.title_id.setText("xyz")
    assert "16 hex digits" in page.message.text()
    assert not page.pack_button.isEnabled()
    page.title_id.setText("0100000000000123")
    assert page.message.text() == ""
    assert page.table.item(0, 1).text() == "0100000000000123"
    assert page.pack_button.isEnabled()


def test_existing_output_blocks_pack(window: MainWindow, dump: Path) -> None:
    (dump.parent / "game.nup").write_bytes(b"precious")
    window.pack_page.set_source(str(dump))
    assert "already exists" in window.pack_page.message.text()
    assert not window.pack_page.pack_button.isEnabled()


def test_split_checks_each_output_it_would_write(window: MainWindow, dump: Path) -> None:
    (dump.parent / "game.dlc.nup").write_bytes(b"precious")
    page = window.pack_page
    page.set_source(str(dump))
    assert page.pack_button.isEnabled()
    page.split.setChecked(True)
    assert "game.dlc.nup already exists" in page.message.text()
    assert not page.pack_button.isEnabled()


def test_update_only_dump_warns_and_the_suggested_workaround_works(window: MainWindow, tmp_path: Path, icon: Path) -> None:
    source = tmp_path / "updates_only"
    (source / "updates/65536/romfs").mkdir(parents=True)
    (source / "updates/65536/romfs/patch.bin").write_bytes(b"patch")
    page = window.pack_page
    page.set_source(str(source))
    assert page.message.text() == ""
    assert page.pack_button.isEnabled()

    page.icon.set_path(str(icon))  # warns in normal mode too, not only split, and for icons
    assert "won't be saved" in page.message.text()
    assert page.pack_button.isEnabled()  # a warning, not an error
    page.icon.clear()
    assert page.message.text() == ""

    # The warning is true: the library drops it.
    out = tmp_path / "updates_only.nup"
    core.run_pack(core.NullContext(), core.PackRequest(str(source), str(out), name="Dropped"))
    assert core.open_container(str(out)).packages[0].name == ""

    # And so is its advice.
    core.run_edit(core.NullContext(), str(out), core.EditRequest(package_index=0, name="Added later"))
    assert core.open_container(str(out)).packages[0].name == "Added later"


def test_localized_rows_are_validated(window: MainWindow, dump: Path) -> None:
    page = window.pack_page
    page.set_source(str(dump))
    page.localized.add_row("", "title")
    assert "language tag" in page.message.text()
    assert not page.pack_button.isEnabled()
    page.localized._rows[0][0].setText("not valid!")
    assert "isn't a valid" in page.message.text()
    page.localized._rows[0][0].setText("JA")
    assert page.pack_button.isEnabled()
    page.localized.add_row("ja", "again")
    assert "twice" in page.message.text()


def test_pack_end_to_end_with_metadata(app, window: MainWindow, dump: Path, dialogs: Dialogs, icon: Path) -> None:
    page = window.pack_page
    page.name.setText("Test Game")
    page.publisher.setText("Acme")
    page.display_version.setText("1.2.3")
    page.icon.set_path(str(icon))
    page.localized.add_row("JA", "テストゲーム")
    page.localized._rows[0][2].set_path(str(icon))
    out = pack_through_ui(app, window, dump)

    assert not dialogs.errors
    assert "Wrote game.nup" in dialogs.boxes[0]
    base = core.open_container(str(out)).packages[0]
    assert base.name == "Test Game"
    assert base.publisher == "Acme"
    assert base.titles == {"ja": "テストゲーム"}
    assert base.icons == {"ja": icon.read_bytes()}
    # The output now exists, so the form must not offer to write it again.
    assert not page.pack_button.isEnabled()
    assert "already exists" in page.message.text()


def test_cancel_pack(app, window: MainWindow, dump: Path, dialogs: Dialogs) -> None:
    window.pack_page.set_source(str(dump))
    window.pack_page.pack_button.click()
    assert window.jobs.busy
    assert not window.pack_page.pack_button.isEnabled()
    window.jobs.cancel()
    wait_idle(app, window)
    assert not dialogs.errors
    assert "cancelled" in window.statusBar().currentMessage()
    assert not Path(window.pack_page.output.text()).exists()
    assert window.pack_page.pack_button.isEnabled()


def test_pack_failure_is_reported(app, window: MainWindow, dump: Path, dialogs: Dialogs, tmp_path: Path) -> None:
    window.pack_page.icon.set_path(str(tmp_path / "missing.png"))
    out = pack_through_ui(app, window, dump)
    assert dialogs.errors and "missing.png" in dialogs.errors[0]
    assert not out.exists()
    assert window.pack_page.pack_button.isEnabled()


# ---- browse page ----


@pytest.fixture
def container(dump: Path, tmp_path: Path, icon: Path) -> Path:
    out = tmp_path / "packed.nup"
    core.run_pack(core.NullContext(), core.PackRequest(
        str(dump), str(out), name="Test Game", publisher="Acme", display_version="1.2.3",
        icon=str(icon), titles={"ja": "テストゲーム"}))
    return out


def detail_text(win: MainWindow) -> list[str]:
    from PySide6.QtWidgets import QLabel
    from PySide6.QtWidgets import QLineEdit
    body = win.browse_page._details_body
    return [w.text() for kind in (QLabel, QLineEdit) for w in body.findChildren(kind)]


def test_open_shows_packages_and_lazy_tree(app, window: MainWindow, container: Path) -> None:
    view = open_through_ui(app, window, container)
    page = window.browse_page
    assert window.tabs.currentWidget() is page
    assert page.title.text() == "Test Game"
    assert "3 packages" in page.summary.text()
    assert page.tree.topLevelItemCount() == 3

    base = page.tree.topLevelItem(0)
    assert base.text(0) == "Base"
    assert base.child(0).data(0, Qt.ItemDataRole.UserRole) == "placeholder"  # not loaded yet
    base.setExpanded(True)
    romfs = find_child(base, "romfs")
    romfs.setExpanded(True)
    assert romfs.child(0).text(0) == "data"  # folders first
    data = find_child(romfs, "data")
    data.setExpanded(True)
    level = find_child(data, "level.bin")
    assert level.text(3) == "zstd"
    assert find_child(romfs, "日本語.txt").text(3) == "raw"
    assert find_child(romfs, "empty.txt").text(3) == "empty"
    assert view.file_count > 0


def test_expanding_twice_does_not_duplicate(app, window: MainWindow, container: Path) -> None:
    open_through_ui(app, window, container)
    base = window.browse_page.tree.topLevelItem(0)
    base.setExpanded(True)
    count = base.childCount()
    base.setExpanded(False)
    base.setExpanded(True)
    assert base.childCount() == count == 2


def test_details_panel_replaces_old_content_immediately(app, window: MainWindow, container: Path) -> None:
    """Regression test: labels from the previous selection stayed visible underneath."""
    open_through_ui(app, window, container)
    page = window.browse_page
    page.tree.setCurrentItem(page.tree.topLevelItem(0))
    assert "Test Game" in detail_text(window)
    page.tree.setCurrentItem(page.tree.topLevelItem(1))
    assert "Test Game" not in detail_text(window)  # with no event loop turn in between


def test_long_hashes_dont_widen_the_details_panel(app, window: MainWindow, container: Path) -> None:
    open_through_ui(app, window, container)
    page = window.browse_page
    base = page.tree.topLevelItem(0)
    base.setExpanded(True)
    exefs = find_child(base, "exefs")
    exefs.setExpanded(True)
    page.tree.setCurrentItem(find_child(exefs, "main"))
    app.processEvents()
    scroll = page._details_scroll
    assert scroll.widget().minimumSizeHint().width() <= scroll.viewport().width()


def test_job_bar_handles_sizes_beyond_32_bits(app, window: MainWindow) -> None:
    from PySide6.QtWidgets import QProgressBar
    bar = window.jobs
    bar._on_progress(5_000_000_000, 10_000_000_000, "x")
    assert bar.findChild(QProgressBar).value() == 5000
    assert "4.7 GB of 9.3 GB" in bar._detail.text()


def test_details_follow_selection(app, window: MainWindow, container: Path) -> None:
    open_through_ui(app, window, container)
    page = window.browse_page
    page.tree.setCurrentItem(page.tree.topLevelItem(0))
    app.processEvents()
    text = detail_text(window)
    assert "Test Game" in text
    assert "0100AAAABBBB0000" in text
    assert any("テストゲーム" in t for t in text)

    base = page.tree.topLevelItem(0)
    base.setExpanded(True)
    exefs = find_child(base, "exefs")
    exefs.setExpanded(True)
    page.tree.setCurrentItem(find_child(exefs, "main"))
    app.processEvents()
    text = detail_text(window)
    assert "main" in text
    assert any(len(t) == 64 for t in text)  # the SHA-256


def test_open_bad_file_reports_and_keeps_current_view(app, window: MainWindow, container: Path,
                                                      dialogs: Dialogs, tmp_path: Path) -> None:
    open_through_ui(app, window, container)
    junk = tmp_path / "junk.nup"
    junk.write_bytes(b"nope" * 100)
    window.open_container(str(junk))
    wait_idle(app, window)
    assert dialogs.errors and "bad magic" in dialogs.errors[0]
    assert window.browse_page.view.path == str(container)
    assert window.browse_page.tree.topLevelItemCount() == 3


def test_verify_ok_and_failed(app, window: MainWindow, container: Path, dialogs: Dialogs) -> None:
    open_through_ui(app, window, container)
    window.browse_page.verify_requested.emit(True)
    wait_idle(app, window)
    assert "Full check passed" in dialogs.boxes[-1]

    window.browse_page.verify_requested.emit(False)
    wait_idle(app, window)
    assert "Quick check passed" in dialogs.boxes[-1]

    from test_core import corrupt_file
    corrupt_file(container, b"random.bin")
    window.browse_page.verify_requested.emit(True)
    wait_idle(app, window)
    assert "Full check failed: 1 problem" in dialogs.boxes[-1]
    assert "random.bin" in dialogs.boxes[-1]


def test_extract_everything_through_ui(app, window: MainWindow, container: Path, dump: Path,
                                       dialogs: Dialogs, tmp_path: Path) -> None:
    open_through_ui(app, window, container)
    dialogs.directory = str(tmp_path / "out")
    window.browse_page.extract_all_requested.emit()
    wait_idle(app, window)
    assert not dialogs.errors
    assert "Extracted" in dialogs.boxes[-1]
    expected = tree_contents(dump)
    del expected["base/exefs/.listing"]
    assert tree_contents(tmp_path / "out") == expected


def test_extract_asks_before_overwriting(app, window: MainWindow, container: Path,
                                         dialogs: Dialogs, tmp_path: Path) -> None:
    open_through_ui(app, window, container)
    dialogs.directory = str(tmp_path / "out")
    window.browse_page.extract_all_requested.emit()
    wait_idle(app, window)
    assert not dialogs.questions

    marker = tmp_path / "out" / "base" / "romfs" / "random.bin"
    marker.write_bytes(b"changed")
    dialogs.answer = QMessageBox.StandardButton.No
    window.browse_page.extract_all_requested.emit()
    assert not window.jobs.busy
    assert "already exist" in dialogs.questions[-1]
    assert marker.read_bytes() == b"changed"

    dialogs.answer = QMessageBox.StandardButton.Yes
    window.browse_page.extract_all_requested.emit()
    wait_idle(app, window)
    assert marker.read_bytes() != b"changed"


def test_extract_cancelled_dialog_does_nothing(app, window: MainWindow, container: Path, dialogs: Dialogs) -> None:
    open_through_ui(app, window, container)
    dialogs.directory = ""
    window.browse_page.extract_all_requested.emit()
    assert not window.jobs.busy
    assert not dialogs.errors


def test_extract_refuses_hostile_container(app, window: MainWindow, dialogs: Dialogs, tmp_path: Path) -> None:
    from test_core import rename_in_pool
    (tmp_path / "d/romfs").mkdir(parents=True)
    (tmp_path / "d/romfs/XXXXXXXX").write_bytes(b"payload")
    out = tmp_path / "evil.nup"
    core.run_pack(core.NullContext(), core.PackRequest(str(tmp_path / "d"), str(out)))
    rename_in_pool(out, b"XXXXXXXX", b"../pwned")
    open_through_ui(app, window, out)

    dialogs.directory = str(tmp_path / "out")
    window.browse_page.extract_all_requested.emit()
    assert not window.jobs.busy
    assert dialogs.errors and "unsafe" in dialogs.errors[0]
    assert not (tmp_path / "pwned").exists()
    assert not (tmp_path / "out").exists()


# ---- edit dialog ----


def test_edit_dialog_sends_only_differences(app, container: Path, icon: Path) -> None:
    pkg = core.open_container(str(container)).packages[0]
    dialog = EditDialog(pkg)
    assert not dialog.request().has_changes()

    dialog.name.setText("Renamed")
    dialog.publisher.setText("")
    req = dialog.request()
    assert req.name == "Renamed"
    assert req.clear_publisher and req.publisher is None
    assert req.display_version is None and not req.clear_display_version
    assert req.icon is None and not req.clear_icon
    assert not req.lang_names and not req.lang_icons


def test_edit_dialog_icon_states(app, container: Path, icon: Path) -> None:
    dialog = EditDialog(core.open_container(str(container)).packages[0])
    dialog.icon.clear()
    assert dialog.request().clear_icon
    dialog.icon.set_path(str(icon))
    req = dialog.request()
    assert req.icon == str(icon) and not req.clear_icon


def test_edit_dialog_removing_a_language_row(app, container: Path) -> None:
    dialog = EditDialog(core.open_container(str(container)).packages[0])
    assert dialog.localized.entries()[0][:2] == ("ja", "テストゲーム")
    dialog.localized._remove_row(dialog.localized._rows[0])
    assert dialog.request().lang_names == {"ja": ""}


def test_edit_dialog_blocks_bad_language(app, container: Path) -> None:
    from PySide6.QtWidgets import QDialogButtonBox
    dialog = EditDialog(core.open_container(str(container)).packages[0])
    dialog.localized.add_row("bad tag", "x")
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()


def test_edit_end_to_end_reloads_view(app, window: MainWindow, container: Path, dialogs: Dialogs,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    open_through_ui(app, window, container)

    def fake_exec(dialog: EditDialog) -> int:
        dialog.name.setText("Renamed")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(EditDialog, "exec", fake_exec)
    window.browse_page.edit_requested.emit(0)
    wait_idle(app, window)   # the save
    wait_idle(app, window)   # the reopen it triggers
    assert not dialogs.errors
    assert window.browse_page.title.text() == "Renamed"
    assert core.open_container(str(container)).packages[0].name == "Renamed"
    assert core.run_verify(core.NullContext(), str(container), True).ok


def test_edit_with_no_changes_does_not_rewrite(app, window: MainWindow, container: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    open_through_ui(app, window, container)
    stamp = container.stat().st_mtime_ns
    monkeypatch.setattr(EditDialog, "exec", lambda _d: QDialog.DialogCode.Accepted)
    window.browse_page.edit_requested.emit(0)
    assert not window.jobs.busy
    assert container.stat().st_mtime_ns == stamp


def test_cancelling_edit_dialog_changes_nothing(app, window: MainWindow, container: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    open_through_ui(app, window, container)
    monkeypatch.setattr(EditDialog, "exec", lambda _d: QDialog.DialogCode.Rejected)
    window.browse_page.edit_requested.emit(0)
    assert not window.jobs.busy


# ---- window behaviour ----


def drop(win: MainWindow, path: Path) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    win.dropEvent(QDropEvent(QPointF(QPoint(5, 5)), Qt.DropAction.CopyAction, mime,
                             Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def test_dropping_a_folder_goes_to_pack(window: MainWindow, dump: Path) -> None:
    window.tabs.setCurrentWidget(window.browse_page)
    drop(window, dump)
    assert window.tabs.currentWidget() is window.pack_page
    assert window.pack_page.source.text() == str(dump)


def test_dropping_a_file_goes_to_browse(app, window: MainWindow, container: Path) -> None:
    drop(window, container)
    wait_idle(app, window)
    assert window.tabs.currentWidget() is window.browse_page
    assert window.browse_page.view.path == str(container)


def test_open_file_is_not_locked_while_browsing(app, window: MainWindow, container: Path) -> None:
    open_through_ui(app, window, container)
    os.replace(container, str(container) + ".moved")  # fails on Windows if still open
    os.replace(str(container) + ".moved", container)


def test_closing_while_busy_asks_then_cancels(app, dialogs: Dialogs, dump: Path) -> None:
    win = MainWindow()
    win.show()
    win.pack_page.set_source(str(dump))
    out = Path(win.pack_page.output.text())
    win.pack_page.pack_button.click()

    dialogs.answer = QMessageBox.StandardButton.No
    assert not win.close()
    assert win.jobs.busy
    assert dialogs.questions

    dialogs.answer = QMessageBox.StandardButton.Yes
    assert not win.close()
    wait_idle(app, win)
    app.processEvents()
    assert not win.isVisible()
    assert not out.exists()


def test_garbage_collection_only_happens_on_the_ui_thread(app, container: Path, dialogs: Dialogs) -> None:
    """Regression test. Automatic GC could run on a worker and destroy Qt objects there,
    which aborted the whole process. Dropped dialogs make cycles for it to find."""
    import gc
    assert not gc.isenabled()

    win = MainWindow()
    win.show()
    for _ in range(20):
        dialog = EditDialog(core.open_container(str(container)).packages[0])
        dialog.localized.add_row("fr", "titre")
    del dialog
    win.browse_page.verify_requested.emit(True)  # nothing open yet: must be a no-op
    open_through_ui(app, win, container)
    win.browse_page.verify_requested.emit(True)
    wait_idle(app, win)
    assert gc.collect() >= 0  # the UI-thread collection the timer does, on those cycles
    app.processEvents()
    assert "Full check passed" in dialogs.boxes[-1]
    win.close()


def test_icons_decode_tolerantly(app) -> None:
    from nup_gui.widgets import scaled_pixmap
    assert scaled_pixmap(PNG, 32) is not None
    assert scaled_pixmap(b"not an image", 32) is None
    assert scaled_pixmap(None, 32) is None

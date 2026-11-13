"""The main window: wires the pages to the background jobs."""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import (QAction, QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent,
                           QKeySequence)
from PySide6.QtWidgets import (QDialog, QFileDialog, QMainWindow, QMessageBox, QTabWidget,
                               QVBoxLayout, QWidget)

from nup.format import NupError

from . import APP_NAME, __version__, core
from .browse_page import BrowsePage, format_count
from .edit_dialog import EditDialog
from .jobs import JobBar
from .pack_page import NUP_FILTER, PackPage


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1040, 720)
        self.setAcceptDrops(True)
        self._close_when_idle = False

        self.pack_page = PackPage()
        self.browse_page = BrowsePage()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.pack_page, "Pack")
        self.tabs.addTab(self.browse_page, "Browse")
        self.jobs = JobBar()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.jobs)
        self.setCentralWidget(central)

        self.pack_page.pack_requested.connect(self.start_pack)
        self.browse_page.open_requested.connect(self.choose_container)
        self.browse_page.verify_requested.connect(self.start_verify)
        self.browse_page.edit_requested.connect(self.edit_metadata)
        self.browse_page.extract_requested.connect(self.extract_selections)
        self.browse_page.extract_all_requested.connect(self.extract_everything)
        self.browse_page.extract_flat_requested.connect(self.extract_flat)
        self.browse_page.error.connect(self._show_error)
        self.jobs.busy_changed.connect(self._set_busy)

        self._build_menu()
        self.statusBar().showMessage("Drop a dump folder to pack it, or a .nup to open it.")

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self._open_action = QAction("&Open .nup…", self)
        self._open_action.setShortcut(QKeySequence.StandardKey.Open)
        self._open_action.triggered.connect(self.choose_container)
        file_menu.addAction(self._open_action)
        file_menu.addSeparator()
        quit_action = QAction("E&xit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)
        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("&About", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _about(self) -> None:
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME} {__version__}</b><br>"
            "A desktop app for the NUP (NX Package) container format.<br><br>"
            "Built on the MIT-licensed nup reference tools:<br>"
            "<a href='https://github.com/a55uka/nup'>github.com/a55uka/nup</a>")

    # ---- routing ----

    def open_path(self, path: str) -> None:
        """Route a path from a drop or the command line to the right tab."""
        if self.jobs.busy:
            return
        if os.path.isdir(path):
            self.pack_page.set_source(path)
            self.tabs.setCurrentWidget(self.pack_page)
        else:
            self.open_container(path)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if not self.jobs.busy and len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        self.open_path(event.mimeData().urls()[0].toLocalFile())
        event.acceptProposedAction()

    def _set_busy(self, busy: bool) -> None:
        self.pack_page.set_busy(busy)
        self.browse_page.set_busy(busy)
        self._open_action.setEnabled(not busy)
        if not busy and self._close_when_idle:
            # Deferred so the finished job's callback runs first. If that starts another
            # job (saving metadata reopens the file), this close waits for it too.
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.jobs.busy:
            event.accept()
            return
        event.ignore()
        if self._close_when_idle:
            return
        answer = QMessageBox.question(
            self, "Still working",
            "An operation is still running. Cancel it and close?")
        if answer == QMessageBox.StandardButton.Yes:
            self._close_when_idle = True
            self.jobs.cancel()

    def _show_error(self, message: str) -> None:
        if self._close_when_idle:
            return
        QMessageBox.critical(self, APP_NAME, message)

    def _status(self, message: str) -> None:
        self.statusBar().showMessage(message)

    # ---- pack ----

    def start_pack(self, request: core.PackRequest) -> None:
        self._status("")
        self.jobs.start(
            f"Packing {os.path.basename(request.output)}",
            lambda ctx: core.run_pack(ctx, request),
            self._pack_finished,
            self._show_error,
            lambda: self._status("Pack cancelled. No output was kept."),
        )

    def _pack_finished(self, result: core.PackResult) -> None:
        self.pack_page.refresh()
        names = ", ".join(os.path.basename(p) for p in result.outputs)
        share = f" ({core.format_share(result.output_size, result.input_size)} of original)" \
            if result.input_size else ""
        summary = f"{core.format_size(result.output_size)}{share} in {result.seconds:.1f}s"
        self._status(f"Packed {names}: {summary}")
        if self._close_when_idle:
            return

        box = QMessageBox(self)
        box.setWindowTitle("Pack finished")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"Wrote {names}")
        box.setInformativeText(f"{format_count(len(result.packages), 'package')}, {summary}")
        open_button = box.addButton("Open", QMessageBox.ButtonRole.AcceptRole)
        show_button = box.addButton("Show in folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        clicked = box.clickedButton()
        box.deleteLater()
        if clicked is open_button:
            self.open_container(result.outputs[0])
        elif clicked is show_button:
            reveal_in_explorer(result.outputs[0])

    # ---- open ----

    def choose_container(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open NUP container", "", NUP_FILTER)
        if path:
            self.open_container(path)

    def open_container(self, path: str, keep_selection: int | None = None) -> None:
        path = os.path.normpath(path)
        self.jobs.start(
            f"Opening {os.path.basename(path)}",
            lambda ctx: core.open_container(path),
            lambda view: self._container_opened(view, keep_selection),
            lambda message: self._show_error(f"Couldn't open {os.path.basename(path)}.\n\n{message}"),
            cancellable=False,
        )

    def _container_opened(self, view: core.ContainerView, keep_selection: int | None) -> None:
        self.browse_page.show_container(view, keep_selection)
        self.tabs.setCurrentWidget(self.browse_page)
        self._status(view.path)

    # ---- verify ----

    def start_verify(self, deep: bool) -> None:
        view = self.browse_page.view
        if view is None:
            return
        self._status("")
        self.jobs.start(
            "Verifying every file" if deep else "Checking structure",
            lambda ctx: core.run_verify(ctx, view.path, deep),
            self._verify_finished,
            self._show_error,
            lambda: self._status("Verify cancelled."),
        )

    def _verify_finished(self, result: core.VerifyResult) -> None:
        if self._close_when_idle:
            return
        kind = "Full check" if result.deep else "Quick check"
        box = QMessageBox(self)
        box.setWindowTitle("Verify")
        if result.ok:
            done = f"{format_count(result.files_checked, 'file')} hashed." if result.deep \
                else "Structure is valid. File contents weren't hashed."
            self._status(f"{kind} passed.")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText(f"{kind} passed")
            box.setInformativeText(done)
        else:
            self._status(f"{kind} failed: {format_count(len(result.problems), 'problem')}.")
            box.setIcon(QMessageBox.Icon.Critical)
            box.setText(f"{kind} failed: {format_count(len(result.problems), 'problem')}")
            box.setInformativeText(result.problems[0])
            box.setDetailedText("\n".join(result.problems))
        box.exec()
        box.deleteLater()

    # ---- edit ----

    def edit_metadata(self, package_index: int) -> None:
        view = self.browse_page.view
        if view is None:
            return
        dialog = EditDialog(view.packages[package_index], self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        request = dialog.request()
        dialog.deleteLater()
        if not accepted:
            return
        if not request.has_changes():
            self._status("No metadata changes to save.")
            return
        path = view.path
        self.jobs.start(
            "Saving metadata",
            lambda ctx: core.run_edit(ctx, path, request),
            lambda _: self.open_container(path, keep_selection=package_index),
            self._show_error,
            cancellable=False,  # the library's swap is atomic; there's no midpoint to stop at
        )

    # ---- extract ----

    def _choose_destination(self) -> str:
        return QFileDialog.getExistingDirectory(self, "Extract to folder")

    def extract_selections(self, selections: list[core.Selection]) -> None:
        view = self.browse_page.view
        if view is None:
            return
        dest = self._choose_destination()
        if dest:
            self._run_plan(lambda: core.plan_extract(view, selections, os.path.normpath(dest)), dest)

    def extract_everything(self) -> None:
        view = self.browse_page.view
        if view is None:
            return
        dest = self._choose_destination()
        if dest:
            selections = [core.Selection("package", p.info.index) for p in view.packages]
            self._run_plan(lambda: core.plan_extract(view, selections, os.path.normpath(dest)), dest)

    def extract_flat(self) -> None:
        view = self.browse_page.view
        if view is None:
            return
        dest = self._choose_destination()
        if dest:
            self._run_plan(lambda: core.plan_flat_extract(view, os.path.normpath(dest)), dest)

    def _run_plan(self, make_plan, dest: str) -> None:
        view = self.browse_page.view
        assert view is not None
        try:
            plan = make_plan()
        except NupError as exc:
            self._show_error(f"Can't extract this: {exc}")
            return
        conflicts = plan.conflicts()
        if conflicts:
            answer = QMessageBox.question(
                self, "Overwrite files?",
                f"{format_count(len(conflicts), 'file')} already exist in the destination, "
                f"including:\n\n{conflicts[0]}\n\nOverwrite them?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        path = view.path
        self._status("")
        self.jobs.start(
            f"Extracting {format_count(len(plan.files), 'file')}",
            lambda ctx: core.run_extract(ctx, path, plan),
            lambda count: self._extract_finished(count, dest),
            self._show_error,
            lambda: self._status("Extract cancelled. Files already written were kept."),
        )

    def _extract_finished(self, count: int, dest: str) -> None:
        self._status(f"Extracted {format_count(count, 'file')} to {os.path.normpath(dest)}")
        if self._close_when_idle:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Extract finished")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"Extracted {format_count(count, 'file')}")
        box.setInformativeText(os.path.normpath(dest))
        show_button = box.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        clicked = box.clickedButton()
        box.deleteLater()
        if clicked is show_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(dest))


def reveal_in_explorer(path: str) -> None:
    if sys.platform == "win32":
        # explorer's exit code is unrelated to success, so don't check it
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

"""The Browse tab: look inside a .nup, and verify, edit or extract from it."""

from __future__ import annotations

import os

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSplitter,
                               QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nup import format as F
from nup.format import NupError

from . import core
from .widgets import muted_label, scaled_pixmap

_ROLE = Qt.ItemDataRole.UserRole
_PLACEHOLDER = "placeholder"
_MAX_DETAIL_ICON = 160


def _right_aligned(item: QTreeWidgetItem, *columns: int) -> None:
    for column in columns:
        item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


class BrowsePage(QWidget):
    open_requested = Signal()
    verify_requested = Signal(bool)  # deep?
    edit_requested = Signal(int)  # package index
    extract_requested = Signal(list)  # list[core.Selection]
    extract_all_requested = Signal()
    extract_flat_requested = Signal()
    error = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view: core.ContainerView | None = None
        self._busy = False

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_empty())
        self._stack.addWidget(self._build_content())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

    def _build_empty(self) -> QWidget:
        page = QWidget()
        title = QLabel("No container open")
        title.setProperty("heading", True)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = muted_label("Open a .nup file, or drop one anywhere on this window.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        button = QPushButton("Open .nup…")
        button.setProperty("primary", True)
        button.clicked.connect(self.open_requested)
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addSpacing(8)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        return page

    def _build_content(self) -> QWidget:
        self.title = QLabel()
        self.title.setProperty("heading", True)
        self.summary = muted_label(wrap=False)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.open_button = QPushButton("Open…")
        self.open_button.clicked.connect(self.open_requested)

        self.verify_button = QPushButton("Verify")
        menu = QMenu(self.verify_button)
        menu.addAction("Full check (hash every file)", lambda: self.verify_requested.emit(True))
        menu.addAction("Quick check (structure only)", lambda: self.verify_requested.emit(False))
        self.verify_button.setMenu(menu)

        self.edit_button = QPushButton("Edit metadata…")
        self.edit_button.clicked.connect(self._emit_edit)

        self.extract_button = QPushButton("Extract")
        self.extract_button.setProperty("primary", True)
        menu = QMenu(self.extract_button)
        self.extract_selected_action = menu.addAction("Selected items…", self._emit_extract)
        menu.addAction("Everything, as a dump folder…", self.extract_all_requested)
        menu.addSeparator()
        menu.addAction("Rescue: every file, ignoring folders…", self.extract_flat_requested)
        self.extract_button.setMenu(menu)

        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        header_text.addWidget(self.title)
        header_text.addWidget(self.summary)
        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 8)
        header.addLayout(header_text, 1)
        header.setSpacing(8)
        for button in (self.open_button, self.verify_button, self.edit_button, self.extract_button):
            button.setMinimumWidth(button.sizeHint().width() + 16)
            header.addWidget(button)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Size", "Stored", "Codec"])
        self.tree.setUniformRowHeights(True)  # keeps large folders fast
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemExpanded.connect(self._populate)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        self.tree.headerItem().setTextAlignment(1, right)
        self.tree.headerItem().setTextAlignment(2, right)
        head = self.tree.header()
        head.setStretchLastSection(False)
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 90), (2, 90), (3, 60)):
            head.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.tree.setColumnWidth(column, width)

        self._details_scroll = QScrollArea()
        self._details_scroll.setWidgetResizable(True)
        self._details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._details_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._clear_details()

        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(self._details_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([620, 380])
        splitter.setChildrenCollapsible(False)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(splitter, 1)
        return page

    # ---- state ----

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_actions()

    def _update_actions(self) -> None:
        ready = not self._busy and self.view is not None
        for button in (self.open_button, self.verify_button, self.extract_button):
            button.setEnabled(not self._busy)
        self.edit_button.setEnabled(ready)
        self.extract_selected_action.setEnabled(ready and bool(self.tree.selectedItems()))

    def show_container(self, view: core.ContainerView, keep_selection: int | None = None) -> None:
        self.view = view
        self.title.setText(self._container_title(view))
        ratio = (f"  ·  {core.format_share(view.file_size, view.total_size)} of original"
                 if view.total_size else "")
        count = len(view.packages)
        self.summary.setText(
            f"{os.path.basename(view.path)}  ·  {format_count(count, 'package')}  ·  "
            f"{format_count(view.file_count, 'file')}  ·  "
            f"{core.format_size(view.file_size)}{ratio}")
        self.summary.setToolTip(view.path)

        self.tree.clear()
        for pkg in view.packages:
            label = pkg.info.kind_name.capitalize()
            if pkg.info.kind == F.KIND_UPDATE:
                label += f" {pkg.version_text}"
            elif pkg.info.kind != F.KIND_BASE:
                label += f" {pkg.info.title_id:016X}"
            item = QTreeWidgetItem([label, core.format_size(pkg.total_size), "", ""])
            _right_aligned(item, 1, 2)
            item.setData(0, _ROLE, core.Selection("package", pkg.info.index))
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            self._add_placeholder(item, pkg.info.root_dir)
            self.tree.addTopLevelItem(item)

        self._stack.setCurrentIndex(1)
        target = keep_selection if keep_selection is not None else 0
        top = self.tree.topLevelItem(min(target, self.tree.topLevelItemCount() - 1))
        if top is not None:
            self.tree.setCurrentItem(top)
            if self.tree.topLevelItemCount() == 1:
                top.setExpanded(True)
        self._update_actions()

    @staticmethod
    def _container_title(view: core.ContainerView) -> str:
        for pkg in view.packages:
            if pkg.name:
                return pkg.name
        return os.path.basename(view.path)

    # ---- tree ----

    def _add_placeholder(self, item: QTreeWidgetItem, dir_index: int) -> None:
        """Children load on first expand, since a RomFS can have many thousands of files."""
        _, _, count = self.view.reader.dirs[dir_index]  # type: ignore[union-attr]
        item.setData(1, _ROLE, dir_index)
        if count:
            marker = QTreeWidgetItem([""])
            marker.setData(0, _ROLE, _PLACEHOLDER)
            item.addChild(marker)

    def _populate(self, item: QTreeWidgetItem) -> None:
        if item.childCount() != 1 or item.child(0).data(0, _ROLE) != _PLACEHOLDER:
            return
        view = self.view
        if view is None:
            return
        item.takeChild(0)
        try:
            children = view.children(item.data(1, _ROLE))
        except NupError as exc:
            self.error.emit(str(exc))
            return
        rows = []
        for child in children:
            if child.is_dir:
                _, size = view.dir_totals[child.index]
                row = QTreeWidgetItem([core.display_name(child.name), core.format_size(size), "", ""])
                row.setData(0, _ROLE, core.Selection("dir", child.index))
                self._add_placeholder(row, child.index)
            else:
                _, size, _sha, _off, stored, codec = view.reader.files[child.index]
                row = QTreeWidgetItem([core.display_name(child.name), core.format_size(size),
                                       core.format_size(stored), codec_name(size, codec)])
                row.setData(0, _ROLE, core.Selection("file", child.index))
            _right_aligned(row, 1, 2)
            rows.append(row)
        item.addChildren(rows)

    def selections(self) -> list[core.Selection]:
        return [item.data(0, _ROLE) for item in self.tree.selectedItems()
                if isinstance(item.data(0, _ROLE), core.Selection)]

    def _selection_changed(self) -> None:
        self._update_actions()
        selected = self.selections()
        self._clear_details()
        if self.view is None or not selected:
            self._details.addStretch(1)
        elif len(selected) > 1:
            self._details.addWidget(muted_label(f"{len(selected)} items selected"))
            self._details.addStretch(1)
        elif selected[0].kind == "package":
            self._show_package(self.view.packages[selected[0].index])
        elif selected[0].kind == "dir":
            self._show_dir(selected[0].index)
        else:
            self._show_file(selected[0].index)

    def _context_menu(self, position: QPoint) -> None:
        if self._busy or not self.selections():
            return
        menu = QMenu(self.tree)
        menu.addAction("Extract…", self._emit_extract)
        selected = self.selections()
        if len(selected) == 1 and selected[0].kind == "package":
            menu.addAction("Edit metadata…", lambda: self.edit_requested.emit(selected[0].index))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _emit_extract(self) -> None:
        selected = self.selections()
        if selected:
            self.extract_requested.emit(selected)

    def _emit_edit(self) -> None:
        self.edit_requested.emit(self.selected_package_index())

    def selected_package_index(self) -> int:
        """The package that contains the current item, else the first package."""
        item = self.tree.currentItem()
        while item is not None and item.parent() is not None:
            item = item.parent()
        if item is not None:
            selection = item.data(0, _ROLE)
            if isinstance(selection, core.Selection):
                return selection.index
        return 0

    # ---- details panel ----

    def _clear_details(self) -> None:
        """Start a fresh panel. setWidget deletes the old one right away, so its labels
        can't linger on screen under the new ones."""
        self._details_body = QWidget()
        self._details = QVBoxLayout(self._details_body)
        self._details.setContentsMargins(16, 12, 16, 12)
        self._details_scroll.setWidget(self._details_body)

    def _add_form(self, rows: list[tuple[str, str]]) -> None:
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        for label, value in rows:
            if not value:
                continue
            if len(value) > 32 and " " not in value:
                # A long hash can't word-wrap, so it would widen the whole panel. A
                # read-only field stays narrow and still selects and copies whole.
                field = QLineEdit(value)
                field.setReadOnly(True)
                field.setFrame(False)
                field.setCursorPosition(0)
                field.setToolTip(value)
                field.setStyleSheet("background: transparent;")
                form.addRow(muted_label(label, wrap=False), field)
                continue
            text = QLabel(value)
            text.setWordWrap(True)
            text.setTextFormat(Qt.TextFormat.PlainText)  # values come from the file
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(muted_label(label, wrap=False), text)
        self._details.addLayout(form)

    def _add_heading(self, text: str) -> None:
        heading = QLabel(text)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setProperty("subheading", True)
        heading.setWordWrap(True)
        self._details.addWidget(heading)

    def _show_package(self, pkg: core.PackageView) -> None:
        pixmap = scaled_pixmap(pkg.icon, _MAX_DETAIL_ICON)
        if pixmap is not None:
            icon = QLabel()
            icon.setPixmap(pixmap)
            self._details.addWidget(icon)
        self._add_heading(pkg.name or f"{pkg.info.kind_name.capitalize()} package")
        kind = pkg.info.kind_name + (" (standalone)" if pkg.standalone else "")
        self._add_form([
            ("Publisher", pkg.publisher),
            ("Kind", kind),
            ("Title ID", f"{pkg.info.title_id:016X}"),
            ("Version", f"{pkg.version_text}  (0x{pkg.info.version:x})"),
            ("Contents", f"{format_count(pkg.file_count, 'file')}, {core.format_size(pkg.total_size)}"),
            ("Build ID", pkg.build_id),
            ("Source dump", pkg.source_dump),
            ("NPDM", "stored" if pkg.has_npdm else ""),
            ("NACP", "stored" if pkg.has_nacp else ""),
        ])
        localized = sorted(pkg.titles.keys() | pkg.icons.keys())
        if localized:
            self._details.addSpacing(8)
            self._add_heading("Localized")
            self._add_form([(lang, (pkg.titles.get(lang, "")
                                    + (" (has icon)" if lang in pkg.icons else "")).strip())
                            for lang in localized])
        self._details.addStretch(1)

    def _show_dir(self, dir_index: int) -> None:
        view = self.view
        assert view is not None
        name, _, count = view.reader.dirs[dir_index]
        files, size = view.dir_totals[dir_index]
        self._add_heading(core.display_name(name))
        self._add_form([("Contains", f"{format_count(files, 'file')}, {core.format_size(size)}"),
                        ("Direct children", f"{count:,}")])
        self._details.addStretch(1)

    def _show_file(self, file_index: int) -> None:
        view = self.view
        assert view is not None
        name, size, sha, _off, stored, codec = view.reader.files[file_index]
        self._add_heading(core.display_name(name))
        self._add_form([
            ("Size", f"{core.format_size(size)}  ({size:,} bytes)"),
            ("Stored", core.format_size(stored)
             + (f"  ({core.format_share(stored, size)})" if size else "")),
            ("Codec", codec_name(size, codec)),
            ("SHA-256", sha.hex()),
        ])
        self._details.addStretch(1)


def codec_name(size: int, codec: int) -> str:
    if not size:
        return "empty"
    return "zstd" if codec == F.CODEC_ZSTD else "raw"


def format_count(n: int, noun: str) -> str:
    return f"{n:,} {noun}" + ("" if n == 1 else "s")

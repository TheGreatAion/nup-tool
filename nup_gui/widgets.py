"""Form pieces shared by the Pack page and the Edit Metadata dialog."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from .core import LANG_TAG_RE

IMAGE_FILTER = "Images (*.jpg *.jpeg *.png);;All files (*)"


def muted_label(text: str = "", wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setProperty("muted", True)
    label.setWordWrap(wrap)
    return label


def scaled_pixmap(data: bytes | None, size: int) -> QPixmap | None:
    """Decode an icon. None if there's no data or Qt can't decode it: the format
    allows any bytes here, so this has to be tolerant."""
    if not data:
        return None
    pixmap = QPixmap()
    if not pixmap.loadFromData(data):
        return None
    return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)


class PathField(QWidget):
    """A line edit with a Browse button. Drops pass through to the main window."""

    changed = Signal(str)

    def __init__(self, mode: str, placeholder: str = "", file_filter: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = mode  # "dir" | "open" | "save"
        self._filter = file_filter
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setAcceptDrops(False)
        self.edit.textChanged.connect(self.changed)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.edit, 1)
        row.addWidget(browse)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, text: str) -> None:
        self.edit.setText(text)

    def _browse(self) -> None:
        current = self.text()
        if self._mode == "dir":
            path = QFileDialog.getExistingDirectory(self, "Choose folder", current)
        elif self._mode == "open":
            path, _ = QFileDialog.getOpenFileName(self, "Choose file", current, self._filter)
        else:
            # The pack step refuses to overwrite, so asking "replace?" here would mislead.
            path, _ = QFileDialog.getSaveFileName(
                self, "Save as", current, self._filter,
                options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self.setText(os.path.normpath(path))


class IconField(QWidget):
    """Thumbnail with Choose and Remove. Remembers whether anything changed, so an
    edit only touches icons the user actually changed."""

    changed = Signal()

    def __init__(self, current: bytes | None = None, thumb: int = 64,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current = current
        self._thumb = thumb
        self._path: str | None = None
        self._removed = False

        self._preview = QLabel()
        self._preview.setFixedSize(thumb, thumb)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setProperty("thumb", True)
        self._choose = QPushButton("Choose…")
        self._choose.clicked.connect(self._pick)
        self._remove = QPushButton("Remove")
        self._remove.clicked.connect(self.clear)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._preview)
        row.addWidget(self._choose)
        row.addWidget(self._remove)
        row.addStretch(1)
        self._refresh()

    def set_path(self, path: str) -> None:
        self._path = path
        self._removed = False
        self._refresh()
        self.changed.emit()

    def clear(self) -> None:
        self._path = None
        self._removed = self._current is not None
        self._refresh()
        self.changed.emit()

    def path(self) -> str:
        return self._path or ""

    def change(self) -> str | None:
        """None: unchanged. A path: replace with that file. Empty string: remove."""
        if self._path:
            return self._path
        return "" if self._removed else None

    def _pick(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose icon", "", IMAGE_FILTER)
        if path:
            self.set_path(path)

    def _refresh(self) -> None:
        if self._path:
            try:
                with open(self._path, "rb") as fh:
                    data: bytes | None = fh.read()
            except OSError:
                data = None
        else:
            data = None if self._removed else self._current
        pixmap = scaled_pixmap(data, self._thumb)
        if pixmap is not None:
            self._preview.setPixmap(pixmap)
        else:
            self._preview.clear()
            self._preview.setText("?" if data else "None")
        self._remove.setEnabled(bool(self._path) or (self._current is not None and not self._removed))
        self._preview.setToolTip(self._path or "")


class LocalizedEditor(QWidget):
    """Rows of language, title and icon: the localized titles and icons of a package."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[QLineEdit, QLineEdit, IconField, QPushButton]] = []
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setColumnStretch(1, 1)
        self._grid.addWidget(muted_label("Language", wrap=False), 0, 0)
        self._grid.addWidget(muted_label("Title", wrap=False), 0, 1)
        self._grid.addWidget(muted_label("Icon", wrap=False), 0, 2)
        self._header_visible(False)

        add = QPushButton("Add language")
        add.clicked.connect(lambda: self.add_row())
        add.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self._grid)
        outer.addWidget(add)

    def _header_visible(self, visible: bool) -> None:
        for column in range(3):
            item = self._grid.itemAtPosition(0, column)
            if item is not None and item.widget() is not None:
                item.widget().setVisible(visible)

    def add_row(self, lang: str = "", title: str = "", icon: bytes | None = None,
                icon_path: str = "") -> None:
        lang_edit = QLineEdit(lang)
        lang_edit.setPlaceholderText("ja")
        lang_edit.setFixedWidth(90)
        lang_edit.setAcceptDrops(False)
        title_edit = QLineEdit(title)
        title_edit.setAcceptDrops(False)
        icon_field = IconField(icon, thumb=32)
        if icon_path:
            icon_field.set_path(icon_path)
        remove = QPushButton("✕")
        remove.setFixedWidth(32)
        remove.setToolTip("Remove this language")
        row = (lang_edit, title_edit, icon_field, remove)
        remove.clicked.connect(lambda: self._remove_row(row))
        lang_edit.textChanged.connect(self.changed)
        title_edit.textChanged.connect(self.changed)
        self._rows.append(row)
        index = self._grid.rowCount()
        for column, widget in enumerate(row):
            self._grid.addWidget(widget, index, column)
        self._header_visible(True)
        self.changed.emit()

    def _remove_row(self, row: tuple[QLineEdit, QLineEdit, IconField, QPushButton]) -> None:
        self._rows.remove(row)
        for widget in row:
            self._grid.removeWidget(widget)
            widget.deleteLater()
        self._header_visible(bool(self._rows))
        self.changed.emit()

    def clear_rows(self) -> None:
        for row in list(self._rows):
            self._remove_row(row)

    def snapshot(self) -> list[tuple[str, str, str]]:
        """Language, title and chosen icon path of every row, for comparing."""
        return [(lang, title, icon.path()) for lang, title, icon in self.entries()]

    def entries(self) -> list[tuple[str, str, IconField]]:
        return [(lang.text().strip().lower(), title.text().strip(), icon)
                for lang, title, icon, _ in self._rows]

    def validation_error(self) -> str:
        seen: set[str] = set()
        for lang, _title, _icon in self.entries():
            if not lang:
                return "Every localized row needs a language tag, such as ja or zh-hans."
            if not LANG_TAG_RE.match(lang) or len(lang) > 255:
                return f"“{lang}” isn't a valid language tag. Use a form like ja or zh-hans."
            if lang in seen:
                return f"Language “{lang}” is listed twice."
            seen.add(lang)
        return ""

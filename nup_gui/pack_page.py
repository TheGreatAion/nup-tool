"""The Pack tab: turn a dump folder into one or more .nup files."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFormLayout, QFrame, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import core
from .widgets import IconField, LocalizedEditor, PathField, muted_label

NUP_FILTER = "NUP containers (*.nup);;All files (*)"


class PackPage(QWidget):
    pack_requested = Signal(object)  # core.PackRequest

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self._output_touched = False
        self._rows: list[core.PackageRow] = []
        self._source_error = ""
        self._short: list[tuple[str, int]] = []

        self.source = PathField("dir", "Choose or drop a dump folder")
        self.source.changed.connect(self._source_changed)
        self.output = PathField("save", "Where to write the .nup", NUP_FILTER)
        self.output.edit.textEdited.connect(self._output_edited)
        self.output.changed.connect(self._validate)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Kind", "Title ID", "Version", "Folder"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setFixedHeight(112)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.table_hint = muted_label(
            "A dump folder holds base/, updates/<version>/ and dlc/<title id>/<index>/, "
            "or just exefs/ and romfs/ directly.")

        self.level = QSpinBox()
        self.level.setRange(1, 22)
        self.level.setValue(3)
        self.level.setFixedWidth(80)
        self.level.setToolTip("Higher levels make smaller files but pack much more slowly.")
        self.split = QCheckBox("Write one file per package group")
        self.split.setToolTip("The base, each update and the DLC each get their own .nup, "
                              "so updates can be swapped or dropped by file.")
        self.split.toggled.connect(self._validate)
        self.keep_listings = QCheckBox("Keep .listing files from the dump tool")

        self.name = QLineEdit()
        self.publisher = QLineEdit()
        self.display_version = QLineEdit()
        self.display_version.setPlaceholderText("1.2.3")
        self.title_id = QLineEdit()
        self.title_id.setPlaceholderText("Read from exefs/main.npdm")
        self.title_id.setMaxLength(16)
        self.title_id.textChanged.connect(self._source_changed)
        self.icon = IconField()
        self.icon.changed.connect(self._validate)
        self.localized = LocalizedEditor()
        self.localized.changed.connect(self._validate)
        for edit in (self.name, self.publisher, self.display_version, self.title_id):
            edit.setAcceptDrops(False)
            edit.textChanged.connect(self._validate)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setProperty("warning", True)
        self.pack_button = QPushButton("Pack")
        self.pack_button.setDefault(True)
        self.pack_button.setProperty("primary", True)
        self.pack_button.setMinimumWidth(120)
        self.pack_button.clicked.connect(self._emit_request)

        self._layout()
        self._validate()

    def _layout(self) -> None:
        source_group = QGroupBox("Source and output")
        form = QFormLayout(source_group)
        form.addRow("Dump folder", self.source)
        form.addRow("Output file", self.output)

        found_group = QGroupBox("Packages found")
        found = QVBoxLayout(found_group)
        found.addWidget(self.table)
        found.addWidget(self.table_hint)

        options_group = QGroupBox("Options")
        options = QFormLayout(options_group)
        options.addRow("Compression level", self.level)
        options.addRow("", self.split)
        options.addRow("", self.keep_listings)

        meta_group = QGroupBox("Metadata (optional)")
        meta = QFormLayout(meta_group)
        meta.addRow("Title", self.name)
        meta.addRow("Publisher", self.publisher)
        meta.addRow("Display version", self.display_version)
        meta.addRow("Title ID override", self.title_id)
        meta.addRow("Icon", self.icon)
        meta.addRow("Localized", self.localized)

        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(16, 16, 16, 16)
        column.setSpacing(12)
        for group in (source_group, found_group, options_group, meta_group):
            column.addWidget(group)
        column.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)

        footer = QHBoxLayout()
        footer.setContentsMargins(16, 8, 16, 12)
        footer.addWidget(self.message, 1)
        footer.addWidget(self.pack_button)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll, 1)
        outer.addLayout(footer)

    def set_source(self, path: str) -> None:
        self.source.setText(os.path.normpath(path))

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._validate()

    def refresh(self) -> None:
        """Re-check the form, e.g. after a pack, when the output now exists."""
        self._validate()

    def _output_edited(self) -> None:
        self._output_touched = True

    def _source_changed(self) -> None:
        source = self.source.text()
        title_id = self.title_id.text().strip()
        self._rows = []
        self._source_error = ""
        if source:
            if title_id and not core.TITLE_ID_RE.match(title_id):
                title_id = ""  # _validate reports it; preview without the override
            try:
                self._rows = core.preview_dump(source, title_id)
            except core.JobError as exc:
                self._source_error = str(exc)
        self._short = core.short_programs(source, [r.folder for r in self._rows]) if self._rows else []
        self._fill_table()
        if source and not self._output_touched:
            clean = os.path.normpath(source)
            parent, name = os.path.split(clean)
            self.output.setText(os.path.join(parent, f"{name or 'game'}.nup"))
        self._validate()

    def _fill_table(self) -> None:
        self.table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            cells = [row.kind, f"{row.title_id:016X}" if row.title_id else "unknown",
                     core.describe_version(row.version), row.folder]
            for j, text in enumerate(cells):
                self.table.setItem(i, j, QTableWidgetItem(text))

    def _error(self) -> str:
        """What's stopping a pack right now, in words. Empty if it can go ahead."""
        source = self.source.text()
        output = self.output.text()
        if not source:
            return ""  # the empty form isn't an error, the disabled button says enough
        if self._source_error:
            return self._source_error
        title_id = self.title_id.text().strip()
        if title_id and not core.TITLE_ID_RE.match(title_id):
            return "Title ID must be exactly 16 hex digits."
        if not output:
            return "Choose an output file."
        try:
            paths = core.output_paths(source, output, self.split.isChecked())
        except core.JobError as exc:
            return str(exc)
        existing = [p for p in paths if os.path.exists(p)]
        if existing:
            return f"{existing[0]} already exists. Choose another output name."
        if not os.path.isdir(os.path.dirname(os.path.abspath(output))):
            return "The output folder doesn't exist."
        return self.localized.validation_error()

    def _damage_text(self) -> str:
        if not self._short:
            return ""
        path, missing = self._short[0]
        more = len(self._short) - 1
        others = f", and {more} more program{'s' if more > 1 else ''} {'are' if more > 1 else 'is'} too" \
            if more else ""
        return (f"This dump is damaged: {path} is {missing:,} bytes shorter than it should be"
                f"{others}. That happens when a dump is copied over FTP in text mode. Copy the "
                f"exefs folders again in binary mode (FileZilla: Transfer > Transfer type > "
                f"Binary), or the game may not run.")

    def _warning(self) -> str:
        """Allowed, but the user should know."""
        warnings = [self._damage_text()]
        has_meta = (any(e.text().strip() for e in (self.name, self.publisher, self.display_version))
                    or bool(self.icon.path()) or bool(self.localized.entries()))
        if has_meta and self._rows and not any(r.kind == "base" for r in self._rows):
            warnings.append("This dump has no base package, so the metadata above won't be saved. "
                            "Pack it first, then add metadata with Edit metadata on the Browse tab.")
        return " ".join(w for w in warnings if w)

    def _validate(self) -> None:
        error = self._error()
        # Damage outranks everything else: it's the one problem a new name or field won't fix.
        self.message.setText(" ".join(t for t in (error, self._damage_text()) if t)
                             if error else self._warning())
        self.pack_button.setEnabled(
            not self._busy and not error and bool(self.source.text()) and bool(self._rows))

    def request(self) -> core.PackRequest:
        titles = {lang: title for lang, title, _ in self.localized.entries() if title}
        icons = {lang: icon.path() for lang, _, icon in self.localized.entries() if icon.path()}
        return core.PackRequest(
            source=self.source.text(),
            output=self.output.text(),
            level=self.level.value(),
            keep_listings=self.keep_listings.isChecked(),
            split=self.split.isChecked(),
            title_id=self.title_id.text().strip(),
            display_version=self.display_version.text().strip(),
            publisher=self.publisher.text().strip(),
            name=self.name.text().strip(),
            icon=self.icon.path(),
            titles=titles,
            icons=icons,
        )

    def _emit_request(self) -> None:
        if self._short:
            answer = QMessageBox.question(
                self, "Pack a damaged dump?",
                f"{self._damage_text()}\n\nPack it anyway?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.pack_requested.emit(self.request())

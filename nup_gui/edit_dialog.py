"""The Edit Metadata dialog for one package of an open container."""

from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QVBoxLayout, QWidget)

from . import core
from .widgets import IconField, LocalizedEditor, muted_label


class EditDialog(QDialog):
    def __init__(self, package: core.PackageView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._package = package
        self.setWindowTitle(f"Edit metadata: {package.label}")
        self.setMinimumWidth(620)

        self.name = QLineEdit(package.name)
        self.publisher = QLineEdit(package.publisher)
        self.display_version = QLineEdit(package.display_version)
        self.icon = IconField(package.icon)
        self.localized = LocalizedEditor()
        for lang in sorted(package.titles.keys() | package.icons.keys()):
            self.localized.add_row(lang, package.titles.get(lang, ""), package.icons.get(lang))
        self.localized.changed.connect(self._validate)

        form = QFormLayout()
        form.addRow("Title", self.name)
        form.addRow("Publisher", self.publisher)
        form.addRow("Display version", self.display_version)
        form.addRow("Icon", self.icon)
        form.addRow("Localized", self.localized)

        self.message = QLabel()
        self.message.setProperty("warning", True)
        self.message.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(muted_label(
            "File contents, title ID and version aren't affected. Saving rewrites the "
            "container, which takes a while for large files."))
        layout.addWidget(self.message)
        layout.addWidget(self.buttons)
        self._validate()

    def _validate(self) -> None:
        error = self.localized.validation_error()
        self.message.setText(error)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(not error)

    def request(self) -> core.EditRequest:
        """Just the differences from the package's current values."""
        pkg = self._package
        req = core.EditRequest(package_index=pkg.info.index)

        name = self.name.text().strip()
        if name != pkg.name:
            req.name, req.clear_name = (name, False) if name else (None, True)
        publisher = self.publisher.text().strip()
        if publisher != pkg.publisher:
            req.publisher, req.clear_publisher = (publisher, False) if publisher else (None, True)
        version = self.display_version.text().strip()
        if version != pkg.display_version:
            req.display_version, req.clear_display_version = \
                (version, False) if version else (None, True)
        icon = self.icon.change()
        if icon == "":
            req.clear_icon = True
        elif icon is not None:
            req.icon = icon

        seen: set[str] = set()
        for lang, title, icon_field in self.localized.entries():
            seen.add(lang)
            if title != pkg.titles.get(lang, ""):
                req.lang_names[lang] = title  # empty removes it
            change = icon_field.change()
            if change is not None:
                req.lang_icons[lang] = change
        for lang in pkg.titles.keys() - seen:
            req.lang_names[lang] = ""
        for lang in pkg.icons.keys() - seen:
            req.lang_icons[lang] = ""
        return req

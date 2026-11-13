"""Entry point: python -m nup_gui [path to a dump folder or .nup]"""

from __future__ import annotations

import gc
import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPalette
from PySide6.QtWidgets import QApplication

from . import APP_NAME
from .main_window import MainWindow

_GC_INTERVAL_MS = 5000

STYLE = """
QLabel[muted="true"] { color: palette(placeholder-text); }
QLabel[warning="true"] { color: %(warning)s; }
QLabel[heading="true"] { font-size: 18px; font-weight: 600; }
QLabel[subheading="true"] { font-size: 14px; font-weight: 600; }
QLabel[thumb="true"] {
    border: 1px solid palette(mid); border-radius: 4px; color: palette(placeholder-text);
}
QPushButton[primary="true"] {
    background: palette(highlight); color: palette(highlighted-text); font-weight: 600;
    border: none; border-radius: 4px; padding: 6px 16px;
}
QPushButton[primary="true"]:hover { background: palette(link); }
QPushButton[primary="true"]:disabled {
    background: palette(midlight); color: palette(placeholder-text);
}
"""


def asset_path(name: str) -> str:
    # In the packaged app, data files live under sys._MEIPASS
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "assets", name)


def collect_garbage_on_ui_thread(app: QApplication) -> None:
    """Python's cycle collector runs on whichever thread happens to be allocating. If
    that's a worker, Qt objects in a garbage cycle get destroyed off the UI thread,
    which aborts the process. So turn automatic collection off and do it from a timer.
    The workers don't create cycles to speak of, so a few seconds' delay costs nothing.
    """
    gc.disable()
    timer = QTimer(app)
    timer.timeout.connect(gc.collect)
    timer.start(_GC_INTERVAL_MS)


def configure(app: QApplication) -> None:
    """App-wide setup. The Fusion style follows the system's light or dark setting."""
    collect_garbage_on_ui_thread(app)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    dark = app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    app.setStyleSheet(STYLE % {"warning": "#fb923c" if dark else "#c2410c"})

    # In dark mode, Windows gives checkboxes their own palette with a navy Base, which
    # makes an unchecked box look filled in. Use the normal field color instead.
    checkbox = QApplication.palette("QCheckBox")
    checkbox.setColor(QPalette.ColorRole.Base, app.palette().base().color())
    app.setPalette(checkbox, "QCheckBox")

    app.setWindowIcon(QIcon(asset_path("icon.ico")))


def main() -> int:
    if sys.platform == "win32":
        # Give the taskbar our icon rather than python.exe's when running from source.
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NupTool.App")

    app = QApplication(sys.argv)
    configure(app)

    window = MainWindow()
    window.show()
    arguments = app.arguments()[1:]
    if arguments:
        path = arguments[0]
        QTimer.singleShot(0, lambda: window.open_path(path))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

"""Runs one long operation at a time on a worker thread, and shows it in a bottom bar."""

from __future__ import annotations

from collections.abc import Callable
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

from nup.format import NupError

from .core import JobCancelled, JobError, format_size

_REPORT_INTERVAL = 0.05  # seconds between UI updates


class _Worker(QThread):
    # object, not int: byte counts overflow a C++ int
    progressed = Signal(object, object, str)
    succeeded = Signal(object)
    failed = Signal(str)
    was_cancelled = Signal()

    def __init__(self, fn: Callable[[Any], Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._cancel = threading.Event()
        self._last_report = 0.0

    def cancel(self) -> None:
        self._cancel.set()

    def report(self, done: int, total: int, text: str = "") -> None:
        """The JobContext handed to core functions. Called from this thread."""
        if self._cancel.is_set():
            raise JobCancelled
        now = time.monotonic()
        if now - self._last_report >= _REPORT_INTERVAL or done == total:
            self._last_report = now
            self.progressed.emit(done, total, text)

    def run(self) -> None:
        try:
            result = self._fn(self)
        except JobCancelled:
            self.was_cancelled.emit()
        except SystemExit as exc:  # how the nup library reports user errors
            self.failed.emit(str(exc.code))
        except (JobError, NupError, OSError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # malformed files can raise anything from the parsers
            self.failed.emit(f"Unexpected {type(exc).__name__}: {exc}")
        else:
            self.succeeded.emit(result)


class JobBar(QWidget):
    """The bottom bar: what's running, how far along it is, and a Cancel button."""

    busy_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: _Worker | None = None
        self._started = 0.0
        self._title = ""

        self._label = QLabel()
        self._detail = QLabel()
        self._detail.setProperty("muted", True)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self.cancel)

        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self._label)
        text.addWidget(self._detail)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 8, 16, 12)
        row.addLayout(text, 2)
        row.addWidget(self._bar, 3)
        row.addWidget(self._cancel)
        self.hide()

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def start(self, title: str, fn: Callable[[Any], Any],
              on_success: Callable[[Any], None],
              on_failure: Callable[[str], None],
              on_cancel: Callable[[], None] | None = None,
              cancellable: bool = True) -> None:
        """Run fn(context) on a worker thread. Callbacks run on the UI thread."""
        if self._worker is not None:
            raise RuntimeError("a job is already running")
        self._title = title
        self._started = time.monotonic()
        self._label.setText(title)
        self._detail.setText("")
        self._bar.setRange(0, 0)
        self._cancel.setEnabled(True)
        self._cancel.setText("Cancel")
        self._cancel.setVisible(cancellable)
        self.show()

        self._on_success = on_success
        self._on_failure = on_failure
        self._on_cancel = on_cancel or (lambda: None)

        # Bound methods of this QObject, not lambdas: that's what makes Qt queue the
        # call onto the UI thread instead of running it on the worker.
        worker = _Worker(fn, self)
        worker.progressed.connect(self._on_progress)
        worker.succeeded.connect(self._job_succeeded)
        worker.failed.connect(self._job_failed)
        worker.was_cancelled.connect(self._job_cancelled)
        self._worker = worker
        self.busy_changed.emit(True)
        worker.start()

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._cancel.setEnabled(False)
            self._cancel.setText("Cancelling…")

    def _on_progress(self, done: int, total: int, text: str) -> None:
        if total <= 0:
            self._bar.setRange(0, 0)
            self._detail.setText(text)
            return
        # scale to 0..10000: the bar stores C++ ints, and byte counts don't fit
        self._bar.setRange(0, 10000)
        self._bar.setValue(int(done / total * 10000))
        parts = [f"{format_size(done)} of {format_size(total)}"]
        elapsed = time.monotonic() - self._started
        if elapsed > 1 and done:
            rate = done / elapsed
            parts.append(f"{format_size(int(rate))}/s")
            if done < total:
                parts.append(_format_duration((total - done) / rate) + " left")
        self._detail.setText("  ·  ".join(parts))
        self._detail.setToolTip(text)

    def _job_succeeded(self, result: Any) -> None:
        self._finish()
        self._on_success(result)

    def _job_failed(self, message: str) -> None:
        self._finish()
        self._on_failure(message)

    def _job_cancelled(self) -> None:
        self._finish()
        self._on_cancel()

    def _finish(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.wait()
            worker.deleteLater()
        self.hide()
        self.busy_changed.emit(False)


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"

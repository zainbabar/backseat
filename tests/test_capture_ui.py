import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from shiboken6 import isValid

from backseat import capture


@pytest.mark.parametrize(
    "choice", [QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.Cancel]
)
def test_selection_removes_native_windows_before_return(monkeypatch, choice):
    app = QApplication.instance() or QApplication([])
    monitor = {"left": 0, "top": 0, "width": 800, "height": 600}
    windows = []
    monkeypatch.setattr(capture, "display_image", lambda monitor: Image.new("RGB", (1600, 1200)))

    def select(dialog):
        windows.append(dialog)
        dialog.show()
        dialog.result_region = capture.Region(monitor, (0.25, 0, 0.75, 1))
        return QDialog.DialogCode.Accepted

    def confirm(dialog):
        windows.append(dialog)
        dialog.show()
        # Reproduce returning from the modal loop while the native window is
        # still present. The public selector must finish cleanup before capture.
        return choice

    monkeypatch.setattr(QDialog, "exec", select)
    monkeypatch.setattr(QMessageBox, "exec", confirm)
    region = capture.select_region(monitor)
    assert (region is not None) == (choice == QMessageBox.StandardButton.Yes)
    assert not any(widget.isVisible() for widget in app.topLevelWidgets())
    assert all(not isValid(widget) for widget in windows)


def test_retry_cleans_previous_windows_before_selecting_again(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monitor = {"left": 0, "top": 0, "width": 800, "height": 600}
    attempts = []
    choices = iter([QMessageBox.StandardButton.Retry, QMessageBox.StandardButton.Yes])
    monkeypatch.setattr(capture, "display_image", lambda monitor: Image.new("RGB", (800, 600)))

    def select(dialog):
        assert not any(widget.isVisible() for widget in app.topLevelWidgets())
        attempts.append(dialog)
        dialog.result_region = capture.Region(monitor, (0, 0, 1, 1))
        return QDialog.DialogCode.Accepted

    def confirm(dialog):
        dialog.show()
        return next(choices)

    monkeypatch.setattr(QDialog, "exec", select)
    monkeypatch.setattr(QMessageBox, "exec", confirm)
    assert capture.select_region(monitor) is not None
    assert len(attempts) == 2
    assert all(not isValid(widget) for widget in attempts)

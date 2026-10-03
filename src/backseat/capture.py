import ctypes
import sys
from dataclasses import dataclass

import mss
from PIL import Image

_app = None


class CaptureError(RuntimeError):
    pass


def check_permission():
    if sys.platform != "darwin":
        raise CaptureError("Backseat v1 requires macOS.")
    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    if not cg.CGPreflightScreenCaptureAccess():
        raise CaptureError(
            "Screen Recording permission is missing. In System Settings → Privacy & Security "
            "→ Screen Recording, enable your terminal (or the app launching Backseat), "
            "then quit and reopen that app."
        )


def monitors():
    with mss.mss() as screen:
        return [dict(monitor) for monitor in screen.monitors[1:]]


def display_image(monitor: dict) -> Image.Image:
    try:
        with mss.mss() as screen:
            shot = screen.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception as exc:
        raise CaptureError(
            "Could not capture the display. Check Screen Recording permission "
            "and whether the selected display is still connected."
        ) from exc


@dataclass(frozen=True)
class Region:
    monitor: dict
    # Fractions of the display, independent of Qt logical or Retina physical pixels.
    bounds: tuple[float, float, float, float]

    def crop(self, image: Image.Image) -> Image.Image:
        left, top, right, bottom = self.bounds
        return image.crop(
            (
                round(left * image.width),
                round(top * image.height),
                round(right * image.width),
                round(bottom * image.height),
            )
        )

    def capture(self):
        if self.monitor not in monitors():
            raise CaptureError("Display layout changed. Use 'region' to select the area again.")
        return self.crop(display_image(self.monitor))


def _dismiss_capture_ui(app):
    """Finish Qt/native window teardown before the caller captures the desktop."""
    from PySide6.QtCore import QEvent, QEventLoop, QTimer

    for widget in app.topLevelWidgets():
        widget.hide()
        widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    # The CLI has no persistent Qt loop. A short event loop lets Cocoa/WindowServer
    # apply the hide before MSS reads the screen; sleeping alone does not pump Qt.
    settling = QEventLoop()
    QTimer.singleShot(200, settling.quit)
    settling.exec()


def select_region(monitor: dict, max_edge: int = 2560) -> Region | None:
    try:
        return _select_region(monitor, max_edge)
    finally:
        if _app is not None:
            _dismiss_capture_ui(_app)


def _select_region(monitor: dict, max_edge: int) -> Region | None:
    global _app
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    _app = QApplication.instance() or QApplication([])
    app = _app
    app.setQuitOnLastWindowClosed(False)
    image = display_image(monitor)
    data = image.tobytes()
    pixmap = QPixmap.fromImage(
        QImage(data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888).copy()
    )
    target = min(
        app.screens(),
        key=lambda s: (
            abs(s.geometry().x() - monitor["left"]) + abs(s.geometry().y() - monitor["top"])
        ),
    )

    class Selector(QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
            )
            self.setGeometry(target.geometry())
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.origin = QPoint()
            self.selection = QRect()
            self.dragging = False
            self.result_region = None

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.drawPixmap(self.rect(), pixmap)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
            if not self.selection.isNull():
                painter.setClipRect(self.selection)
                painter.drawPixmap(self.rect(), pixmap)
                painter.setClipping(False)
                painter.setPen(QPen(QColor("#ff6655"), 2))
                painter.drawRect(self.selection)
            painter.setPen(QColor("white"))
            painter.drawText(24, 36, "backseat — drag an area to watch · Esc to cancel")

        def mousePressEvent(self, event):
            if event.button() == Qt.MouseButton.LeftButton:
                self.origin = event.position().toPoint()
                self.dragging = True

        def mouseMoveEvent(self, event):
            if self.dragging:
                self.selection = QRect(self.origin, event.position().toPoint()).normalized()
                self.selection = self.selection.intersected(self.rect())
                self.update()

        def mouseReleaseEvent(self, event):
            if not self.dragging or event.button() != Qt.MouseButton.LeftButton:
                return
            self.dragging = False
            self.selection = QRect(self.origin, event.position().toPoint()).normalized()
            self.selection = self.selection.intersected(self.rect())
            box = self.selection
            if box.width() < 64 or box.height() < 64:
                return
            self.result_region = Region(
                monitor,
                (
                    box.x() / self.width(),
                    box.y() / self.height(),
                    (box.x() + box.width()) / self.width(),
                    (box.y() + box.height()) / self.height(),
                ),
            )
            self.accept()

    while True:
        selector = Selector()
        if selector.exec() != QDialog.DialogCode.Accepted:
            return None
        region = selector.result_region
        preview = region.crop(image)
        original_size = preview.size
        preview.thumbnail((max_edge, max_edge))
        raw = preview.tobytes()
        preview_pixmap = QPixmap.fromImage(
            QImage(
                raw, preview.width, preview.height, preview.width * 3, QImage.Format.Format_RGB888
            ).copy()
        )
        dialog = QMessageBox()
        dialog.setWindowTitle("Backseat capture preview")
        dialog.setText(
            f"Watch this area? Capture: {original_size[0]}×{original_size[1]} pixels\n"
            f"Model input: {preview.width}×{preview.height} pixels. Check text readability."
        )
        dialog.setIconPixmap(
            preview_pixmap.scaled(
                1200,
                650,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.Retry
            | QMessageBox.StandardButton.Cancel
        )
        choice = dialog.exec()
        if choice == QMessageBox.StandardButton.Yes:
            return region
        if choice != QMessageBox.StandardButton.Retry:
            return None
        _dismiss_capture_ui(app)

"""Click-through subtitle bubble for delivered remarks.

The bubble is a real on-screen window, so screen captures include it. It is only ever
placed outside the watched region; otherwise the observer would read its own captions
back as screen activity. When the region leaves no room on its display, captions are
skipped rather than overlapping it.
"""

import time

from .capture import set_focusable

MARGIN = 24
MAX_WIDTH = 720
MIN_WIDTH = 280


def region_rect(region):
    """The watched region in global display points (the coordinate space Qt windows use)."""
    monitor = region.monitor
    left, top, right, bottom = region.bounds
    return (
        monitor["left"] + left * monitor["width"],
        monitor["top"] + top * monitor["height"],
        monitor["left"] + right * monitor["width"],
        monitor["top"] + bottom * monitor["height"],
    )


def placement(region, measure):
    """Return (x, y, width) for a bubble beside the region, or None if nothing fits.

    measure(width) returns the bubble height when wrapped to that width. Bands are tried
    below, above, right, then left of the region, each inset by MARGIN on every side.
    """
    monitor = region.monitor
    screen_left, screen_top = monitor["left"], monitor["top"]
    screen_right = screen_left + monitor["width"]
    screen_bottom = screen_top + monitor["height"]
    left, top, right, bottom = region_rect(region)

    def centered(width, start, end):
        center = (left + right) / 2
        lowest, highest = start + MARGIN, end - MARGIN - width
        return min(max(center - width / 2, lowest), highest)

    width = min(MAX_WIDTH, monitor["width"] - 2 * MARGIN)
    if width >= MIN_WIDTH:
        height = measure(width)
        x = centered(width, screen_left, screen_right)
        if screen_bottom - bottom >= height + 2 * MARGIN:
            return round(x), round(screen_bottom - MARGIN - height), width
        if top - screen_top >= height + 2 * MARGIN:
            return round(x), round(screen_top + MARGIN), width
    for band_left, band_right in ((right, screen_right), (screen_left, left)):
        width = min(MAX_WIDTH, int(band_right - band_left) - 2 * MARGIN)
        if width < MIN_WIDTH:
            continue
        height = measure(width)
        if height + 2 * MARGIN <= monitor["height"]:
            y = min(
                max((top + bottom) / 2 - height / 2, screen_top + MARGIN),
                screen_bottom - MARGIN - height,
            )
            return round(band_left + MARGIN), round(y), width
    return None


class Captions:
    """Main-thread only. The CLI loop pumps Qt events through update()."""

    def __init__(self, clock=time.monotonic, emit=print):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        self.app.setQuitOnLastWindowClosed(False)
        # Showing a caption must never take keyboard focus from the user's apps. A window
        # created before the event loop applies this policy change never appears on screen.
        set_focusable(False)
        self.app.processEvents()
        self.clock, self.emit = clock, emit
        self.widget = None
        self.hide_at = None
        self.warned = False

    def _label(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel
        from shiboken6 import isValid

        # Region reselection tears down every top-level Qt window, including this one.
        if self.widget is not None and isValid(self.widget):
            return self.widget
        label = QLabel()
        label.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        label.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # Tool windows otherwise vanish on macOS whenever another app (the terminal) is active.
        label.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            "QLabel { background: rgba(18, 18, 20, 225); color: white; border-radius: 14px;"
            " padding: 14px 20px; font-size: 20px; }"
        )
        # Apply the stylesheet font now so heightForWidth measures the text as drawn.
        label.ensurePolished()
        self.widget = label
        return label

    def show(self, text, region, seconds=None):
        """Show text beside region. seconds=None keeps it up until linger() or hide()."""
        label = self._label()
        label.setText(text)

        def measure(width):
            label.setFixedWidth(width)
            return label.heightForWidth(width)

        spot = placement(region, measure)
        if spot is None:
            label.hide()
            if not self.warned:
                self.warned = True
                self.emit("Captions skipped: no room beside the watched region on its display.")
            return False
        x, y, width = spot
        label.setFixedSize(width, measure(width))
        label.move(x, y)
        label.show()
        label.raise_()
        self.hide_at = None if seconds is None else self.clock() + seconds
        self.app.processEvents()
        return True

    def linger(self, seconds):
        if self.widget is not None and self.hide_at is None:
            self.hide_at = self.clock() + seconds

    def hide(self):
        from shiboken6 import isValid

        self.hide_at = None
        if self.widget is not None and isValid(self.widget):
            self.widget.hide()
        self.app.processEvents()

    @property
    def visible(self):
        from shiboken6 import isValid

        return self.widget is not None and isValid(self.widget) and self.widget.isVisible()

    def update(self):
        if self.hide_at is not None and self.clock() >= self.hide_at:
            self.hide()
        else:
            self.app.processEvents()

    def close(self):
        from shiboken6 import isValid

        self.hide()
        if self.widget is not None and isValid(self.widget):
            self.widget.deleteLater()
        self.widget = None

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent
from shiboken6 import isValid

from backseat.captions import MARGIN, Captions, placement, region_rect
from backseat.capture import Region

MONITOR = {"left": 100, "top": 50, "width": 2000, "height": 1000}


def overlaps(region, spot, height):
    left, top, right, bottom = region_rect(region)
    x, y, width = spot
    return not (x + width <= left or x >= right or y + height <= top or y >= bottom)


@pytest.mark.parametrize(
    ("bounds", "side"),
    [
        ((0.2, 0.0, 0.8, 0.6), "below"),
        ((0.2, 0.4, 0.8, 1.0), "above"),
        ((0.0, 0.0, 0.6, 1.0), "right"),
        ((0.4, 0.0, 1.0, 1.0), "left"),
    ],
)
def test_caption_goes_beside_region_without_overlap(bounds, side):
    region = Region(MONITOR, bounds)
    spot = placement(region, lambda width: 120)
    assert spot is not None
    assert not overlaps(region, spot, 120)
    x, y, width = spot
    left, top, right, bottom = region_rect(region)
    assert {
        "below": y >= bottom + MARGIN,
        "above": y + 120 <= top - MARGIN,
        "right": x >= right + MARGIN,
        "left": x + width <= left - MARGIN,
    }[side]
    assert MONITOR["left"] <= x and x + width <= MONITOR["left"] + MONITOR["width"]
    assert MONITOR["top"] <= y and y + 120 <= MONITOR["top"] + MONITOR["height"]


def test_no_caption_when_region_fills_the_display():
    assert placement(Region(MONITOR, (0.02, 0.02, 0.98, 0.98)), lambda width: 120) is None


def test_widget_is_placed_outside_region_and_survives_qt_teardown():
    captions = Captions(clock=lambda: 0, emit=lambda message: None)
    region = Region(MONITOR, (0.2, 0.0, 0.8, 0.6))
    assert captions.show("The test passed. I'll notify the historical society.", region)
    widget = captions.widget
    geometry = widget.geometry()
    spot = (geometry.x(), geometry.y(), geometry.width())
    assert geometry.height() > 0 and not overlaps(region, spot, geometry.height())
    # Region reselection deletes every top-level Qt window; the next caption recovers.
    widget.deleteLater()
    captions.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(widget)
    assert captions.show("Back again.", region)
    assert captions.visible
    captions.close()


def test_caption_hides_after_linger_and_reports_when_skipped():
    now = [0.0]
    messages = []
    captions = Captions(clock=lambda: now[0], emit=messages.append)
    assert captions.show("Audio remark.", Region(MONITOR, (0.2, 0.0, 0.8, 0.6)))
    captions.update()
    assert captions.visible  # Spoken captions stay until playback ends.
    captions.linger(2)
    now[0] = 1.9
    captions.update()
    assert captions.visible
    now[0] = 2
    captions.update()
    assert not captions.visible
    assert not captions.show("No room.", Region(MONITOR, (0, 0, 1, 1)))
    assert not captions.show("Still no room.", Region(MONITOR, (0, 0, 1, 1)))
    assert len(messages) == 1
    captions.close()

import pytest
from PIL import Image
from pydantic import ValidationError

from backseat.capture import Region
from backseat.core import Commentary, Config, Session, changed, thumbnail


def test_scaled_region_crop_preserves_physical_pixels():
    region = Region({"width": 2560, "height": 720}, (0.25, 0, 0.75, 1))
    image = Image.new("RGB", (5120, 1440))
    assert region.crop(image).size == (2560, 1440)


def test_changes_accumulate_against_submitted_image():
    previous = thumbnail(Image.new("RGB", (256, 144), "black"))
    image = Image.new("RGB", (256, 144), "black")
    image.paste("white", (0, 0, 2, 144))
    assert not changed(previous, thumbnail(image), 0.02)
    image.paste("white", (0, 0, 8, 144))
    assert changed(previous, thumbnail(image), 0.02)
    assert changed(None, thumbnail(image), 0.02)


def test_commentary_rejects_long_or_empty_spoken_remarks():
    for remark in ("", "word " * 36):
        with pytest.raises(ValidationError):
            Commentary(observation="Editor visible", speak=True, remark=remark)
    assert Commentary(observation="Editor visible", speak=False, remark="discard").remark == ""


def test_history_is_bounded_and_region_reset_clears_it():
    session = Session(Config())
    for index in range(10):
        session.record(Commentary(observation=str(index), speak=False, remark=""))
    assert len(session.history) == 6
    assert session.history[0]["observation"] == "4"
    session.invalidate(reset_history=True)
    assert not session.history
    assert session.epoch == 1


def test_invalid_config_is_rejected():
    with pytest.raises(ValidationError):
        Config(sample_seconds=0)

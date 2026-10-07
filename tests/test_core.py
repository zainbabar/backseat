import pytest
from PIL import Image
from pydantic import ValidationError

from backseat.capture import Region
from backseat.core import (
    Candidate,
    Config,
    Event,
    EventProposal,
    Joke,
    Observation,
    Remark,
    Task,
    changed,
    thumbnail,
)


def test_scaled_region_crop_preserves_physical_pixels():
    region = Region({"width": 2560, "height": 720}, (0.25, 0, 0.75, 1))
    assert region.crop(Image.new("RGB", (5120, 1440))).size == (2560, 1440)


def test_changes_accumulate_against_successful_image():
    previous = thumbnail(Image.new("RGB", (256, 144), "black"))
    image = Image.new("RGB", (256, 144), "black")
    image.paste("white", (0, 0, 2, 144))
    assert not changed(previous, thumbnail(image), 0.02)
    image.paste("white", (0, 0, 8, 144))
    assert changed(previous, thumbnail(image), 0.02)
    assert changed(None, thumbnail(image), 0.02)


def test_invalid_writer_outputs_rejected():
    for remark in ("", "word " * 36):
        with pytest.raises(ValidationError):
            Candidate(remark=remark, premise="empty function", supporting_event_ids=["e1"])
    with pytest.raises(ValidationError):
        Joke(speak=True, candidates=[], selected_index=0)
    assert Joke(speak=False, candidates=[], selected_index=-1).selected is None


def test_prompt_context_is_bounded_and_excludes_corrected_events():
    task = Task()
    task.events = [Event(description=str(i), evidence="screen") for i in range(30)]
    task.remarks = [Remark(text=str(i), premise="test", supporting_event_ids=[]) for i in range(30)]
    task.events[-1].valid = False
    context = task.context()
    assert len(context["events"]) == len(context["remarks"]) == 20
    assert context["events"][0]["description"] == "9"
    context["events"][0]["description"] = "edited"
    assert task.events[9].description == "9"


def test_resolution_keeps_historical_fact_for_callbacks():
    task = Task(events=[Event(description="test failed", evidence="FAIL")])
    old = task.events[0]
    observation = Observation(
        activity="tests passing",
        summary="Failure resolved",
        unresolved=[],
        events=[
            EventProposal(
                description="test passed", evidence="PASS", uncertain=False, noteworthy=True
            )
        ],
        superseded_event_ids=[old.id],
    )
    fresh = task.apply(observation, "2026-10-03T00:00:00+00:00")
    assert not old.active and old.valid
    assert fresh == [task.events[-1].id]
    assert task.events[-1].time == "2026-10-03T00:00:00+00:00"


def test_unknown_supersession_and_invalid_config_rejected():
    with pytest.raises(ValueError):
        Task().apply(
            Observation(
                activity="editor",
                summary="editor",
                unresolved=[],
                events=[],
                superseded_event_ids=["invented-id"],
            ),
            "now",
        )
    with pytest.raises(ValidationError):
        Config(sample_seconds=0)


def test_resume_reestablishes_current_scene_without_rewriting_history():
    old = Event(description="editor open", evidence="visible editor", time="yesterday")
    task = Task(events=[old])
    observation = Observation(
        activity="editor",
        summary="editor open",
        unresolved=[],
        superseded_event_ids=[],
        events=[
            EventProposal(
                description="editor open",
                evidence="visible editor",
                uncertain=False,
                noteworthy=False,
            )
        ],
    )
    assert task.apply(observation, "today") == []
    fresh = task.apply(observation, "today", refresh_current=True)
    assert old.time == "yesterday" and not old.active
    assert fresh == [task.events[-1].id]
    assert task.events[-1].time == "today"

from concurrent.futures import Future

import pytest
from PIL import Image

from backseat.core import Candidate, Config, EventProposal, Joke, ModelOutputError, Observation
from backseat.memory import MemoryStore, MemoryStoreError
from backseat.runtime import Runner


class ManualExecutor:
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        future = Future()
        self.jobs.append((future, fn, args))
        return future

    def finish(self, error=None):
        future, fn, args = self.jobs.pop(0)
        if error:
            future.set_exception(error)
        else:
            future.set_result(fn(*args))

    def shutdown(self, **kwargs):
        pass


class FakeRegion:
    def __init__(self):
        self.calls = 0
        self.image = Image.new("RGB", (800, 600), "black")

    def capture(self):
        self.calls += 1
        return self.image.copy()


class FakeProviders:
    def __init__(self):
        self.speech_calls = 0
        self.observation = Observation(
            activity="editor",
            summary="An empty function is visible",
            unresolved=["function empty"],
            events=[
                EventProposal(
                    description="Empty function",
                    evidence="function has pass body",
                    uncertain=False,
                    noteworthy=True,
                )
            ],
            superseded_event_ids=[],
        )
        self.joke = None
        self.contexts = []
        self.writer_contexts = []

    def observe(self, image, previous, context):
        self.contexts.append((previous, context))
        return self.observation

    def write(self, trigger, context, preferences):
        self.writer_contexts.append(context)
        if self.joke:
            return self.joke
        return Joke(
            speak=True,
            candidates=[
                Candidate(
                    remark="That empty function is really carrying the team.",
                    premise="empty function",
                    supporting_event_ids=[trigger["id"]],
                )
                for _ in range(3)
            ],
            selected_index=0,
        )

    def synthesize(self, remark):
        self.speech_calls += 1
        return b"audio"

    def close(self):
        pass


class FakePlayer:
    def __init__(self):
        self.starts = 0
        self.status = None
        self.stopped = False

    def start(self, audio):
        self.starts += 1
        self.status = None

    def poll(self):
        return self.status

    def stop(self):
        self.stopped = True


@pytest.fixture
def setup():
    now = [0.0]
    providers, region, player = FakeProviders(), FakeRegion(), FakePlayer()
    runner = Runner(
        Config(sample_seconds=15, cooldown_seconds=15),
        providers,
        region,
        player=player,
        clock=lambda: now[0],
        emit=lambda message: None,
    )
    runner.observer_executor.shutdown()
    runner.writer_executor.shutdown()
    runner.observer_executor, runner.writer_executor = ManualExecutor(), ManualExecutor()
    yield runner, now, providers, region, player
    runner.close()


def observe(runner):
    runner.tick()
    runner.observer_executor.finish()
    runner.tick()


def play(runner):
    observe(runner)
    runner.writer_executor.finish()
    runner.tick()
    runner.writer_executor.finish()
    runner.tick()


def test_no_queued_or_unchanged_observations(setup):
    runner, now, _, region, _ = setup
    runner.tick()
    now[0] = 30
    runner.tick()
    assert region.calls == 1
    assert len(runner.observer_executor.jobs) == 1
    runner.observer_executor.finish()
    runner.tick()
    assert region.calls == 2  # Local check; no second request for unchanged screen.
    assert runner.observer_pending is None


def test_observer_continues_during_audio_and_cooldown(setup):
    runner, now, providers, region, player = setup
    play(runner)
    assert player.starts == 1
    region.image = Image.new("RGB", (800, 600), "white")
    now[0] = 15
    runner.tick()
    assert region.calls == 2 and runner.observer_pending is not None
    providers.observation = Observation(
        activity="tests",
        summary="Tests now pass",
        unresolved=[],
        events=[
            EventProposal(
                description="Test passed", evidence="PASS", uncertain=False, noteworthy=True
            )
        ],
        superseded_event_ids=[],
    )
    runner.observer_executor.finish()
    runner.tick()
    assert runner.session.task.summary == "Tests now pass"
    assert runner.writer_pending is None  # Playback still in progress.
    player.status = 0
    now[0] = 20
    runner.tick()
    assert runner.session.last_spoken == 20
    now[0] = 34
    runner.tick()
    assert runner.writer_pending is None
    now[0] = 35
    runner.tick()
    assert runner.writer_pending is not None


@pytest.mark.parametrize("stage", ["observer", "writer", "speech"])
def test_pause_discards_pending_output(setup, stage):
    runner, _, providers, _, player = setup
    runner.tick()
    executor = runner.observer_executor
    if stage != "observer":
        executor.finish()
        runner.tick()
        executor = runner.writer_executor
    if stage == "speech":
        executor.finish()
        runner.tick()
    runner.pause()
    executor.finish()
    runner.tick()
    assert player.starts == 0
    assert not runner.session.task.remarks
    if stage != "speech":
        assert providers.speech_calls == 0


def test_reselection_preserves_memory_but_resets_images(setup):
    runner, _, _, _, _ = setup
    observe(runner)
    task_id = runner.session.task.id
    runner.reselect(FakeRegion())
    assert runner.session.task.id == task_id
    assert runner.session.task.events
    assert runner.session.previous_image is None
    runner.writer_executor.finish()
    runner.tick()
    assert not runner.session.task.remarks


def test_new_task_discards_observation_in_flight(setup):
    runner, _, _, _, _ = setup
    runner.tick()
    old_id = runner.session.task.id
    runner.new_task("binary search practice")
    runner.observer_executor.finish()
    runner.tick()
    assert runner.session.task.id != old_id
    assert runner.session.task.goal == "binary search practice"
    assert not runner.session.task.events


def test_silence_and_uncertainty_do_not_call_writer(setup):
    runner, _, providers, _, _ = setup
    providers.observation.events[0].uncertain = True
    observe(runner)
    assert runner.writer_pending is None
    assert providers.speech_calls == 0
    assert runner.session.task.events[0].uncertain


def test_writer_silence_never_calls_speech(setup):
    runner, _, providers, _, _ = setup
    providers.joke = Joke(speak=False, candidates=[], selected_index=-1)
    observe(runner)
    runner.writer_executor.finish()
    runner.tick()
    assert runner.writer_pending is None
    assert providers.speech_calls == 0


def test_resolution_invalidates_joke_before_audio(setup):
    runner, now, providers, region, player = setup
    observe(runner)
    trigger_id = runner.trigger[0]
    runner.writer_executor.finish()
    runner.tick()  # Speech is generating.
    providers.observation = Observation(
        activity="browser",
        summary="Left the editor",
        unresolved=[],
        events=[],
        superseded_event_ids=[trigger_id],
    )
    region.image = Image.new("RGB", (800, 600), "white")
    now[0] = 15
    runner.tick()
    runner.observer_executor.finish()
    runner.writer_executor.finish()
    runner.tick()
    assert not runner.event(trigger_id).active
    assert player.starts == 0
    assert not runner.session.task.remarks


def test_correction_invalidates_old_facts_and_pending_joke(setup):
    runner, _, _, _, player = setup
    observe(runner)
    runner.add_context("That is a stub, not a failed implementation", correction=True)
    runner.writer_executor.finish()
    runner.tick()
    assert all(not e.valid for e in runner.session.task.events)
    assert "stub" in runner.session.task.context_notes[-1]
    assert player.starts == 0


def test_unsupported_and_repeated_jokes_are_rejected(setup):
    runner, _, providers, _, _ = setup
    observe(runner)
    fake = Candidate(remark="A made up failure.", premise="invented", supporting_event_ids=["fake"])
    providers.joke = Joke(speak=True, candidates=[fake] * 3, selected_index=0)
    runner.writer_executor.finish()
    runner.tick()
    assert not runner.session.task.remarks
    assert providers.speech_calls == 0


def test_failures_retry_observation_at_next_interval_and_auth_pauses(setup):
    runner, now, _, region, _ = setup
    runner.tick()
    runner.observer_executor.finish(error=ValueError("bad output"))
    runner.tick()
    assert not runner.session.paused
    runner.tick()
    assert region.calls == 1
    now[0] = 15
    runner.tick()
    assert runner.observer_pending is not None

    class Unauthorized(Exception):
        status_code = 401

    runner.observer_executor.finish(error=Unauthorized())
    runner.tick()
    assert runner.session.paused


def test_stale_writer_is_not_spoken(setup):
    runner, now, _, _, player = setup
    observe(runner)
    now[0] = 61
    runner.writer_executor.finish()
    runner.tick()
    assert player.starts == 0 and not runner.session.task.remarks


def test_feedback_interruption_and_persistence(setup, tmp_path):
    runner, _, _, _, player = setup
    store = MemoryStore(tmp_path / "memory.sqlite")
    runner.memory = store
    play(runner)
    runner.pause()
    runner.feedback("funny")
    saved = store.last_task()
    assert player.stopped
    assert saved.remarks[-1].interrupted
    assert saved.remarks[-1].feedback == "funny"
    assert store.preferences() and "funny" in store.preferences()[-1]
    runner.memory = None
    store.close()


def test_storage_failure_pauses_without_erasing_existing_file(setup):
    runner, _, _, _, _ = setup

    class BrokenStore:
        def save(self, *args):
            raise MemoryStoreError("Could not save task memory.")

    runner.memory = BrokenStore()
    observe(runner)
    assert runner.session.paused
    assert runner.had_error


def test_exact_repeat_is_not_delivered_again(setup):
    runner, now, providers, region, player = setup
    runner.text_only = True
    observe(runner)
    runner.writer_executor.finish()
    runner.tick()
    assert len(runner.session.task.remarks) == 1
    old_id = runner.trigger[0]
    providers.observation = Observation(
        activity="new editor",
        summary="A second empty function",
        unresolved=[],
        events=[
            EventProposal(
                description="Second empty function",
                evidence="another pass body",
                uncertain=False,
                noteworthy=True,
            )
        ],
        superseded_event_ids=[old_id],
    )
    region.image = Image.new("RGB", (800, 600), "white")
    now[0] = 15
    observe(runner)
    runner.writer_executor.finish()
    runner.tick()
    assert len(runner.session.task.remarks) == 1
    assert player.starts == 0


def test_once_exits_after_expired_observation(setup):
    runner, now, _, _, _ = setup
    runner.once = True
    runner.tick()
    now[0] = 61
    runner.observer_executor.finish()
    runner.tick()
    assert runner.finished and runner.had_error


def test_observe_only_never_calls_writer(setup):
    runner, _, _, _, _ = setup
    runner.observe_only = True
    observe(runner)
    assert runner.session.task.events
    assert runner.writer_pending is None


def test_completed_audio_resets_partial_delivery_marker(setup):
    runner, _, _, _, player = setup
    play(runner)
    assert runner.session.task.remarks[-1].interrupted
    player.status = 0
    runner.tick()
    assert not runner.session.task.remarks[-1].interrupted


def test_callback_can_reference_historical_event_but_trigger_must_stay_current(setup):
    from backseat.core import Event

    runner, _, providers, _, _ = setup
    runner.text_only = True
    historical = Event(description="test failed earlier", evidence="FAIL", active=False)
    runner.session.task.events.append(historical)
    observe(runner)
    trigger_id = runner.trigger[0]
    candidate = Candidate(
        remark="The test passed. I'll notify the historical society.",
        premise="payoff",
        supporting_event_ids=[historical.id, trigger_id],
    )
    providers.joke = Joke(speak=True, candidates=[candidate] * 3, selected_index=0)
    runner.writer_executor.finish()
    runner.tick()
    assert runner.session.task.remarks[-1].supporting_event_ids == [historical.id, trigger_id]


def test_ready_audio_waits_for_newer_observation_before_playback(setup):
    runner, now, providers, region, player = setup
    observe(runner)
    trigger_id = runner.trigger[0]
    runner.writer_executor.finish()
    runner.tick()
    region.image = Image.new("RGB", (800, 600), "white")
    now[0] = 15
    runner.tick()
    runner.writer_executor.finish()
    runner.tick()
    assert player.starts == 0 and runner.writer_pending is not None
    providers.observation = Observation(
        activity="browser",
        summary="Moved on",
        unresolved=[],
        events=[],
        superseded_event_ids=[trigger_id],
    )
    runner.observer_executor.finish()
    runner.tick()
    assert player.starts == 0 and runner.writer_pending is None


def test_shutdown_cancels_pending_work_in_both_stages(setup):
    runner, now, _, region, _ = setup
    observe(runner)
    region.image = Image.new("RGB", (800, 600), "white")
    now[0] = 15
    runner.tick()
    observation = runner.observer_pending.future
    writer = runner.writer_pending.future
    runner.close()
    assert observation.cancelled() and writer.cancelled()


def test_ordinary_certain_event_reaches_writer(setup):
    runner, _, providers, _, _ = setup
    providers.observation.events[0].noteworthy = False
    observe(runner)
    assert runner.writer_pending is not None


def test_chatty_repeats_on_unchanged_screen_despite_silence_and_cooldown(setup):
    runner, now, providers, region, _ = setup
    runner.chatty = runner.text_only = True
    runner.session.config.sample_seconds = 10
    runner.session.config.cooldown_seconds = 60
    providers.observation.events = []
    providers.joke = Joke(speak=False, candidates=[], selected_index=-1)
    for at in (0, 10, 20):
        now[0] = at
        observe(runner)
        runner.writer_executor.finish()
        runner.tick()
    assert region.calls == 3
    assert len(runner.session.task.remarks) == 3
    assert all(context["testing_commentary"] for context in providers.writer_contexts)
    assert all("supervision" in remark.text for remark in runner.session.task.remarks)


def test_force_remark_only_forces_first_cycle_and_can_speak(setup):
    runner, now, providers, region, player = setup
    runner.force_remark = True
    providers.observation.events = []
    providers.joke = Joke(speak=False, candidates=[], selected_index=-1)
    play(runner)
    assert player.starts == providers.speech_calls == 1
    assert not runner.force_remark
    player.status = 0
    now[0] = 15
    runner.tick()
    assert region.calls == 2  # Unchanged local check, no second provider request.
    assert runner.observations_submitted == 1
    assert runner.writer_pending is None


def test_forced_output_does_not_starve_on_newer_frames_or_overlap_audio(setup):
    runner, now, _, _, player = setup
    runner.chatty = True
    observe(runner)
    now[0] = 15
    runner.tick()  # New observer while first writer is still pending.
    runner.observer_executor.finish()
    runner.tick()
    assert len(runner.writer_executor.jobs) == 1
    runner.writer_executor.finish()
    runner.tick()
    runner.writer_executor.finish()
    runner.tick()
    assert player.starts == 1
    assert runner.writer_pending is None  # Latest trigger waits for playback.
    player.status = 0
    runner.tick()
    assert runner.writer_pending is not None  # No post-speech cooldown in testing.


@pytest.mark.parametrize("stage", ["observer", "writer", "speech"])
def test_forced_output_respects_pause_at_every_stage(setup, stage):
    runner, _, providers, region, player = setup
    runner.chatty = True
    runner.tick()
    executor = runner.observer_executor
    if stage != "observer":
        executor.finish()
        runner.tick()
        executor = runner.writer_executor
    if stage == "speech":
        executor.finish()
        runner.tick()
    calls = region.calls
    runner.pause()
    executor.finish()
    runner.tick()
    assert region.calls == calls
    assert player.starts == 0 and not runner.session.task.remarks
    if stage != "speech":
        assert providers.speech_calls == 0


def test_observe_only_overrides_forced_commentary(setup):
    runner, _, _, _, _ = setup
    runner.chatty = runner.force_remark = runner.observe_only = True
    observe(runner)
    assert runner.writer_pending is None
    assert len(runner.session.task.events) == 1  # No testing-control event saved.


def test_forced_unknown_evidence_uses_generic_fallback(setup):
    runner, _, providers, _, _ = setup
    runner.force_remark = runner.text_only = True
    fake = Candidate(
        remark="An invented failure.", premise="invented", supporting_event_ids=["fake"]
    )
    providers.joke = Joke(speak=True, candidates=[fake] * 3, selected_index=0)
    observe(runner)
    runner.writer_executor.finish()
    runner.tick()
    assert "supervision" in runner.session.task.remarks[-1].text


def test_forced_writer_still_expires(setup):
    runner, now, _, _, player = setup
    runner.force_remark = True
    observe(runner)
    now[0] = 61
    runner.writer_executor.finish()
    runner.tick()
    assert player.starts == 0 and not runner.session.task.remarks


@pytest.mark.parametrize("text_only", [True, False])
def test_forced_malformed_writer_response_reaches_fallback_without_retry(setup, text_only):
    runner, _, _, _, player = setup
    runner.force_remark = True
    runner.text_only = text_only
    observe(runner)
    runner.writer_executor.finish(
        error=ModelOutputError("response did not match the required structure")
    )
    runner.tick()
    if not text_only:
        runner.writer_executor.finish()
        runner.tick()
        assert player.starts == 1
    assert len(runner.session.task.remarks) == 1
    assert "supervision" in runner.session.task.remarks[0].text
    assert not runner.had_error
    assert not runner.writer_executor.jobs


def test_forced_malformed_observation_does_not_apply_old_scene_as_current(setup):
    runner, _, providers, _, _ = setup
    runner.force_remark = runner.text_only = True
    providers.joke = Joke(speak=False, candidates=[], selected_index=-1)
    runner.tick()
    runner.observer_executor.finish(
        error=ModelOutputError("response did not match the required structure")
    )
    runner.tick()
    runner.writer_executor.finish()
    runner.tick()
    assert runner.session.previous_image is None
    assert "no claims about the current scene" in providers.writer_contexts[0]["summary"]
    assert len(runner.session.task.remarks) == 1 and not runner.had_error


def test_regular_validation_failure_has_accurate_private_error_message(setup):
    from pydantic import ValidationError

    runner, _, _, _, _ = setup
    messages = []
    runner.emit = messages.append
    observe(runner)
    try:
        Candidate(remark="PRIVATE " * 36, premise="private", supporting_event_ids=["e1"])
    except ValidationError as exc:
        runner.writer_executor.finish(error=exc)
    runner.tick()
    assert "model output rejected" in messages[-1]
    assert "PRIVATE" not in messages[-1] and "quota" not in messages[-1]
    assert runner.had_error and not runner.session.task.remarks


@pytest.mark.parametrize("paused", [False, True])
def test_forced_mode_does_not_fallback_on_authentication_or_invalidated_output(setup, paused):
    runner, _, _, _, player = setup
    runner.force_remark = True
    observe(runner)
    if paused:
        runner.pause()
        error = ModelOutputError("response did not match the required structure")
    else:

        class Unauthorized(Exception):
            status_code = 401

        error = Unauthorized()
    runner.writer_executor.finish(error=error)
    runner.tick()
    assert runner.session.paused
    assert player.starts == 0 and not runner.session.task.remarks


class FakeCaptions:
    def __init__(self):
        self.calls = []

    def show(self, text, region, seconds=None):
        self.calls.append(("show", text, seconds))
        return True

    def linger(self, seconds):
        self.calls.append(("linger", seconds))

    def hide(self):
        self.calls.append(("hide",))

    def update(self):
        pass


def test_captions_follow_audio_and_hide_on_invalidation(setup):
    runner, _, _, _, player = setup
    runner.captions = captions = FakeCaptions()
    play(runner)
    assert ("show", "That empty function is really carrying the team.", None) in captions.calls
    player.status = 0
    runner.tick()
    assert captions.calls[-1] == ("linger", 2.0)
    runner.pause()
    assert captions.calls[-1] == ("hide",)


def test_text_only_captions_get_reading_time(setup):
    runner, _, _, _, _ = setup
    runner.captions = captions = FakeCaptions()
    runner.text_only = True
    observe(runner)
    runner.writer_executor.finish()
    runner.tick()
    shown = [call for call in captions.calls if call[0] == "show"]
    assert shown and shown[0][2] >= 4


def recap_result(runner, **overrides):
    from backseat.core import Recap

    ids = [event.id for event in runner.session.task.events]
    fields = dict(speak=True, remark="What a journey. The function stayed empty.")
    fields["supporting_event_ids"] = ids
    fields.update(overrides)
    return Recap(**fields)


def test_recap_is_skipped_without_events_from_this_session(setup):
    from backseat.core import Event

    runner, _, providers, _, _ = setup
    runner.session.task.events.append(
        Event(time="2000-01-01T00:00:00+00:00", description="old failure", evidence="FAIL")
    )
    providers.recap = lambda *args: pytest.fail("No recap request without session events")
    runner.recap()
    assert not runner.session.task.remarks


def test_text_recap_uses_only_certain_session_events_and_is_saved(setup):
    from backseat.core import Event
    from backseat.runtime import CONTROL_DESCRIPTION

    runner, _, providers, _, player = setup
    runner.text_only = True
    observe(runner)
    real = runner.session.task.events[-1].id
    runner.session.task.events.append(Event(description=CONTROL_DESCRIPTION, evidence="flag"))
    received = []

    def recap(events, context, preferences):
        received.append(events)
        return recap_result(runner, supporting_event_ids=[real])

    providers.recap = recap
    runner.recap()
    assert [event["id"] for event in received[0]] == [real]
    assert runner.session.task.remarks[-1].premise == "session recap"
    assert providers.speech_calls == 0 and player.starts == 0


def test_recap_rejects_events_outside_this_session(setup):
    runner, _, providers, _, _ = setup
    runner.text_only = True
    observe(runner)
    providers.recap = lambda *args: recap_result(runner, supporting_event_ids=["invented"])
    runner.recap()
    assert not runner.session.task.remarks
    assert runner.had_error


def test_spoken_recap_waits_for_playback_and_records_completion(setup):
    runner, _, providers, _, player = setup
    observe(runner)
    runner.writer_pending = None  # Recap must not depend on regular commentary.
    providers.recap = lambda *args: recap_result(runner)

    def finished_start(audio):
        player.starts += 1
        player.status = 0

    player.start = finished_start
    runner.recap()
    assert providers.speech_calls == 1 and player.starts == 1
    assert runner.session.task.remarks[-1].interrupted is False

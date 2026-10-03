from concurrent.futures import Future

import pytest
from PIL import Image

from backseat.core import Commentary, Config
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
        self.calls = 0
        self.speech_calls = 0
        self.commentary = Commentary(
            observation="An editor is visible",
            speak=True,
            remark="That empty function is really carrying the team.",
        )

    def observe(self, image, history):
        self.calls += 1
        return self.commentary

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
        Config(), providers, region, player=player, clock=lambda: now[0], emit=lambda message: None
    )
    runner.executor.shutdown()
    runner.executor = ManualExecutor()
    yield runner, now, providers, region, player
    runner.close()


def test_no_overlapping_requests_and_no_unchanged_requests(setup):
    runner, now, providers, region, _ = setup
    runner.text_only = True
    runner.tick()
    now[0] = 90
    runner.tick()
    assert region.calls == 1
    assert len(runner.executor.jobs) == 1
    # Response has become stale; discard it.
    runner.executor.finish()
    runner.tick()
    assert not runner.session.history
    runner.tick()
    assert region.calls == 2
    assert not runner.pending


def test_cooldown_starts_when_audio_finishes(setup):
    runner, now, _, region, player = setup
    runner.tick()
    runner.executor.finish()
    runner.tick()
    runner.executor.finish()
    runner.tick()
    assert player.starts == 1
    now[0] = 100
    runner.tick()
    assert region.calls == 1
    player.status = 0
    runner.tick()
    assert runner.session.last_spoken == 100
    region.image = Image.new("RGB", (800, 600), "white")
    now[0] = 159
    runner.tick()
    assert region.calls == 1
    now[0] = 160
    runner.tick()
    assert region.calls == 2


@pytest.mark.parametrize("stage", ["model", "speech"])
def test_pause_resume_discards_pending_output(setup, stage):
    runner, _, providers, _, player = setup
    runner.tick()
    if stage == "speech":
        runner.executor.finish()
        runner.tick()
    runner.pause()
    runner.resume()
    runner.executor.finish()
    runner.tick()
    assert player.starts == 0
    assert all(not entry["remark"] for entry in runner.session.history)
    if stage == "model":
        assert providers.speech_calls == 0


def test_silence_never_calls_speech(setup):
    runner, _, providers, _, player = setup
    providers.commentary = Commentary(observation="An unchanged editor", speak=False, remark="")
    runner.tick()
    runner.executor.finish()
    runner.tick()
    assert providers.speech_calls == 0
    assert player.starts == 0
    assert len(runner.session.history) == 1


def test_auth_failure_pauses_and_other_failure_waits(setup):
    runner, now, _, region, _ = setup
    runner.tick()
    runner.executor.finish(error=ValueError("invalid model output"))
    runner.tick()
    runner.tick()
    assert region.calls == 1
    assert not runner.session.paused
    # Retry after an inference failure even if the screenshot stays unchanged.
    now[0] = 30
    runner.tick()
    assert runner.pending is not None

    class Unauthorized(Exception):
        status_code = 401

    runner.executor.finish(error=Unauthorized())
    runner.tick()
    assert runner.session.paused


def test_reselection_resets_history_and_discards_old_response(setup):
    runner, _, _, _, player = setup
    runner.session.record(Commentary(observation="Old app", speak=False, remark=""))
    runner.tick()
    runner.reselect(FakeRegion())
    runner.executor.finish()
    runner.tick()
    assert not runner.session.history
    assert player.starts == 0


def test_pause_stops_audio_and_close_cancels_pending(setup):
    runner, _, _, _, player = setup
    runner.tick()
    runner.executor.finish()
    runner.tick()
    runner.executor.finish()
    runner.tick()
    runner.pause()
    assert player.stopped
    assert runner.playing_entry is None
    runner.resume()
    runner.session.last_spoken = float("-inf")
    runner.tick()
    pending = runner.pending.future
    runner.close()
    assert pending.cancelled()

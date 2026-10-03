import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .core import Session, changed, thumbnail


class Player:
    def __init__(self):
        self.process = None
        self.path = None

    def start(self, audio: bytes):
        self.stop()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            self.path = Path(handle.name)
            handle.write(audio)
        try:
            self.process = subprocess.Popen(
                ["afplay", str(self.path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception:
            self.stop()
            raise

    def poll(self):
        return self.process.poll() if self.process else None

    def stop(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process = None
        if self.path:
            self.path.unlink(missing_ok=True)
            self.path = None


def auth_error(exc):
    return getattr(exc, "status_code", None) in (401, 403)


@dataclass
class Pending:
    future: object
    epoch: int
    started: float
    stage: str
    entry: dict | None = None
    remark: str = ""


class Runner:
    """One worker, no queued frames, with invalidation across pause/reselection."""

    def __init__(
        self,
        config,
        providers,
        region,
        *,
        text_only=False,
        capture_dir=None,
        player=None,
        clock=time.monotonic,
        emit=print,
    ):
        self.session = Session(config)
        self.providers = providers
        self.region = region
        self.text_only = text_only
        self.capture_dir = capture_dir
        self.player = player or Player()
        self.clock = clock
        self.emit = emit
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.pending = None
        self.playing_entry = None
        self.closed = False
        self.had_error = False

    def pause(self):
        self.session.paused = True
        self.session.invalidate()
        self.stop_playback()
        self.emit("Paused. No new captures or API calls; pending output will be discarded.")

    def resume(self):
        self.session.paused = False
        self.session.next_sample = 0
        self.emit("Resumed.")

    def reselect(self, region):
        self.session.invalidate(reset_history=True)
        self.region = region
        self.session.next_sample = 0

    def stop_playback(self):
        if self.playing_entry is not None:
            self.session.last_spoken = self.clock()
            self.playing_entry = None
        self.player.stop()

    def fail(self, exc):
        # Do not print provider exception bodies: these may include submitted content.
        self.had_error = True
        self.session.previous = None
        self.session.next_sample = self.clock() + self.session.config.sample_seconds
        if auth_error(exc):
            self.pause()
            self.emit(
                "Provider authorization failed (401/403). Check keys, model/voice access, "
                "then restart Backseat."
            )
        else:
            self.emit(
                f"Skipped cycle ({type(exc).__name__}). Check connection, provider quota, "
                "or configuration; next attempt follows the sampling interval."
            )

    def tick(self):
        now = self.clock()
        if self.playing_entry is not None:
            status = self.player.poll()
            if status is None:
                return
            if status != 0:
                self.emit("Audio playback failed; check your output device and afplay.")
            self.stop_playback()

        if self.pending:
            pending = self.pending
            if not pending.future.done():
                return
            self.pending = None
            valid = (
                not self.closed
                and not self.session.paused
                and pending.epoch == self.session.epoch
                and now - pending.started <= self.session.config.stale_seconds
            )
            try:
                result = pending.future.result()
            except Exception as exc:
                if valid:
                    self.fail(exc)
                return
            if not valid:
                return
            if pending.stage == "model":
                entry = self.session.record(result)
                if self.text_only:
                    self.emit(f"saw: {result.observation}")
                if not result.speak:
                    self.emit("Backseat stays quiet.")
                elif self.text_only:
                    self.emit(f"backseat: {result.remark}")
                    entry["remark"] = result.remark
                    self.session.last_spoken = now
                else:
                    future = self.executor.submit(self.providers.synthesize, result.remark)
                    self.pending = Pending(
                        future, pending.epoch, pending.started, "speech", entry, result.remark
                    )
            else:
                try:
                    self.player.start(result)
                except Exception as exc:
                    self.fail(exc)
                    return
                self.emit(f"backseat: {pending.remark}")
                pending.entry["remark"] = pending.remark
                self.playing_entry = pending.entry
            return

        if not self.session.eligible(now) or self.closed:
            return
        self.session.next_sample = now + self.session.config.sample_seconds
        try:
            image = self.region.capture()
        except Exception as exc:
            self.had_error = True
            self.pause()
            self.emit(str(exc))
            return
        current = thumbnail(image)
        if not changed(self.session.previous, current, self.session.config.change_threshold):
            return
        # Compare against the last submitted image, so small changes can accumulate.
        self.session.previous = current
        if self.capture_dir:
            try:
                self.capture_dir.mkdir(parents=True, exist_ok=True)
                image.save(self.capture_dir / f"capture-{time.time_ns()}.png")
            except OSError as exc:
                self.fail(exc)
                return
        future = self.executor.submit(self.providers.observe, image, list(self.session.history))
        self.pending = Pending(future, self.session.epoch, now, "model")

    def close(self):
        self.closed = True
        self.session.invalidate()
        self.stop_playback()
        if self.pending:
            self.pending.future.cancel()
        # In-flight HTTP requests cannot be recalled; timeouts bound shutdown.
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.providers.close()

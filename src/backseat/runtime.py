import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .core import (
    Candidate,
    Event,
    ModelOutputError,
    Observation,
    Remark,
    Session,
    Task,
    changed,
    thumbnail,
    timestamp,
)
from .memory import MemoryStoreError

CONTROL_DESCRIPTION = "Testing mode requested commentary on this captured scene"
CAPTION_LINGER_SECONDS = 2.0


class Player:
    def __init__(self):
        self.process = None
        self.path = None

    def start(self, audio: bytes):
        self.stop()
        # afplay detects the format from content, so `say` AIFF also plays from this file.
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
    image: object = None
    observed_at: str = ""
    trigger_id: str = ""
    candidate: object = None
    allowed_ids: frozenset = frozenset()
    forced: bool = False


class Runner:
    """One worker per stage; no queued frames or remarks; only main-thread state writes."""

    def __init__(
        self,
        config,
        providers,
        region,
        *,
        task=None,
        preferences=None,
        memory=None,
        text_only=False,
        observe_only=False,
        once=False,
        chatty=False,
        force_remark=False,
        capture_dir=None,
        captions=None,
        player=None,
        clock=time.monotonic,
        emit=print,
    ):
        self.session = Session(config, task=task or Task(), preferences=preferences or [])
        self.providers, self.region, self.memory = providers, region, memory
        self.text_only, self.observe_only, self.once = text_only, observe_only, once
        self.chatty, self.force_remark = chatty, force_remark
        self.capture_dir, self.captions = capture_dir, captions
        self.player, self.clock, self.emit = player or Player(), clock, emit
        self.started_at = timestamp()
        self.observer_executor = ThreadPoolExecutor(max_workers=1)
        self.writer_executor = ThreadPoolExecutor(max_workers=1)
        self.observer_pending = None
        self.writer_pending = None
        self.playing_entry = None
        self.trigger = None  # Latest eligible event, never a queue.
        self.previous_captured_at = None
        self.attempted = set()
        self.observations_submitted = 0
        self.observations_completed = 0
        self.closed = self.had_error = False

    @property
    def finished(self):
        return (
            self.had_error
            or self.session.paused
            or (
                self.observations_completed > 0
                and self.observer_pending is None
                and self.writer_pending is None
                and self.playing_entry is None
            )
        )

    def save(self):
        if self.memory is not None:
            self.memory.save(self.session.task, self.session.preferences)

    def invalidate(self):
        self.session.invalidate()
        self.previous_captured_at = None
        self.trigger = None
        self.stop_playback(interrupted=True)
        if self.captions:
            self.captions.hide()

    def pause(self):
        self.session.paused = True
        self.invalidate()
        self.emit("Paused. Pending output will be discarded; no new captures or API calls.")

    def resume(self):
        self.session.paused = False
        self.session.next_sample = 0
        self.emit("Resumed.")

    def reselect(self, region):
        self.invalidate()
        self.region = region
        self.session.next_sample = 0
        self.emit("Region updated. Task memory retained; image comparison reset.")

    def stop_playback(self, *, interrupted=False):
        self.player.stop()
        if self.playing_entry is not None:
            self.playing_entry.interrupted = interrupted
            self.session.last_spoken = self.clock()
            self.session.task.updated = timestamp()
            self.playing_entry = None
            self.save()

    def new_task(self, goal):
        self.invalidate()
        self.save()
        self.session.task = Task(goal=goal)
        self.attempted.clear()
        self.session.next_sample = 0
        self.save()
        self.emit("New task started.")

    def add_context(self, text, *, correction=False):
        self.invalidate()
        task = self.session.task
        if correction:
            # No factual claim from the mistaken story should seed future callbacks.
            for event in task.events:
                event.valid = False
            task.summary = (
                "User corrected prior understanding. Re-establish facts from new captures."
            )
            task.unresolved = []
            task.activity = ""
            if task.remarks:
                task.remarks[-1].feedback = "wrong: " + text
        task.context_notes = (
            task.context_notes + [("User correction: " if correction else "User context: ") + text]
        )[-20:]
        task.updated = timestamp()
        self.session.next_sample = 0
        self.save()
        self.emit("Correction saved." if correction else "Task context saved.")

    def taste(self, text):
        self.invalidate()
        self.session.preferences = (self.session.preferences + [text])[-20:]
        self.session.task.updated = timestamp()
        self.save()
        self.emit("Humor preference saved.")

    def feedback(self, rating):
        if not self.session.task.remarks:
            self.emit("No delivered remark to rate yet.")
            return
        self.invalidate()
        self.session.task.remarks[-1].feedback = rating
        remark = self.session.task.remarks[-1]
        self.session.preferences = (
            self.session.preferences
            + [
                f"User rated this joke {rating} (style example only): {remark.text} "
                f"Premise: {remark.premise}"
            ]
        )[-20:]
        self.session.task.updated = timestamp()
        self.save()
        self.emit(f"Marked last delivered remark {rating}.")

    def fail(self, exc, stage):
        # Provider exception bodies may contain submitted content. Never log them.
        self.had_error = True
        if stage == "observer":
            self.session.previous = None
            self.session.next_sample = self.clock() + self.session.config.sample_seconds
        if isinstance(exc, MemoryStoreError):
            self.session.paused = True
            self.session.invalidate()
            self.trigger = None
            self.player.stop()
            self.playing_entry = None
            self.emit(str(exc))
        elif auth_error(exc):
            self.pause()
            self.emit(
                "Provider authorization failed (401/403). Check keys/model/voice access "
                "and restart Backseat."
            )
        elif isinstance(exc, (ModelOutputError, ValidationError)):
            reason = (
                str(exc) if isinstance(exc, ModelOutputError) else "invalid structured response"
            )
            self.emit(f"Skipped {stage}: model output rejected ({reason}). No immediate retry.")
        else:
            self.emit(
                f"Skipped {stage} ({type(exc).__name__}). Check connection, quota, "
                "or configuration. No immediate automatic retry."
            )

    def valid(self, pending, now):
        return (
            not self.closed
            and not self.session.paused
            and pending.epoch == self.session.epoch
            and now - pending.started <= self.session.config.stale_seconds
        )

    def event(self, event_id):
        return next((event for event in self.session.task.events if event.id == event_id), None)

    def relevant(self, pending, now):
        trigger = self.event(pending.trigger_id)
        return (
            self.valid(pending, now)
            and trigger is not None
            and trigger.valid
            and not trigger.uncertain
            and self.trigger is not None
            and (pending.forced or (trigger.active and self.trigger[0] == pending.trigger_id))
        )

    def candidate_valid(self, pending):
        candidate = pending.candidate
        if candidate is None:
            return False
        ids = set(candidate.supporting_event_ids)
        if pending.trigger_id not in ids or not ids.issubset(pending.allowed_ids):
            return False
        if any(
            (event := self.event(event_id)) is None or not event.valid or event.uncertain
            for event_id in ids
        ):
            return False
        normalized = " ".join(candidate.remark.casefold().split())
        return pending.forced or not any(
            " ".join(remark.text.casefold().split()) == normalized
            for remark in self.session.task.remarks
        )

    def delivered(self, candidate, *, audio=False):
        remark = Remark(
            text=candidate.remark,
            premise=candidate.premise,
            supporting_event_ids=candidate.supporting_event_ids,
            # Treat delivery as partial until afplay reports successful completion.
            interrupted=audio,
        )
        self.session.task.remarks.append(remark)
        self.session.task.updated = timestamp()
        self.emit(f"backseat: {remark.text}")
        if self.captions:
            # Spoken captions stay up until playback ends; printed ones get reading time.
            seconds = None if audio else reading_seconds(remark.text)
            self.captions.show(remark.text, self.region, seconds)
        if audio:
            self.playing_entry = remark
        else:
            self.session.last_spoken = self.clock()
        self.save()

    def poll_observer(self, now):
        pending = self.observer_pending
        if pending is None or not pending.future.done():
            return
        self.observer_pending = None
        if not self.valid(pending, now):
            # Consume exceptions without applying old output to the current task.
            try:
                pending.future.result()
            except Exception:
                pass
            if self.once:
                self.had_error = True
                self.emit("Observation expired before completion; try once again.")
            return
        try:
            observation_rejected = False
            try:
                observation = pending.future.result()
                # Apply transactionally so invalid IDs cannot partially mutate memory.
                task = self.session.task.model_copy(deep=True)
                fresh = task.apply(
                    observation,
                    pending.observed_at,
                    refresh_current=self.observations_completed == 0,
                )
            except (ModelOutputError, ValidationError) as exc:
                if not pending.forced or self.observe_only:
                    self.fail(exc, "observer")
                    return
                self.emit(
                    "Observer output rejected; testing remark will have no current scene facts."
                )
                observation_rejected = True
                observation = Observation(
                    activity="Current capture could not be interpreted reliably",
                    summary="Current image could not be interpreted. Earlier events are history; "
                    "no claims about the current scene are available.",
                    unresolved=[],
                    events=[],
                    superseded_event_ids=[],
                )
                task = self.session.task.model_copy(deep=True)
                fresh = task.apply(observation, pending.observed_at)
            if pending.forced and not self.observe_only:
                # A real user request, not a fabricated change on the desktop.
                control = Event(
                    time=pending.observed_at,
                    description=CONTROL_DESCRIPTION,
                    evidence="User enabled forced commentary; no task progress is implied.",
                )
                task.events.append(control)
                fresh = [control.id]
            self.session.task = task
            # Playback's record belongs to the latest copied task too.
            if self.playing_entry:
                self.playing_entry = next(r for r in task.remarks if r.id == self.playing_entry.id)
            if not observation_rejected:
                self.session.previous_image = pending.image
                self.session.previous = thumbnail(pending.image)
                self.previous_captured_at = pending.observed_at
            self.observations_completed += 1
            self.save()
            if self.text_only or self.observe_only:
                self.emit(f"saw: {observation.activity}")
            if self.observe_only:
                self.emit("Observer complete; --observe-only skips writer and speech.")
                return
            if fresh:
                self.trigger = (fresh[-1], pending.started, pending.forced)
                if pending.forced:
                    self.emit("Observer complete; testing mode requested a remark.")
                else:
                    self.emit(f"Observer: {len(fresh)} new certain event(s); writer eligible.")
            else:
                uncertain = sum(event.uncertain for event in observation.events)
                self.emit(
                    f"Observer: no new certain events ({len(observation.events)} reported, "
                    f"{uncertain} uncertain); writer skipped."
                )
        except Exception as exc:
            self.fail(exc, "observer")

    def poll_writer(self, now):
        pending = self.writer_pending
        if pending is None or not pending.future.done():
            return
        # A newer frame is already being interpreted. Wait for its facts before
        # spending on speech or delivering a joke about the previous situation.
        if (
            self.observer_pending is not None
            and self.observer_pending.started > pending.started
            and self.valid(pending, now)
            and not pending.forced
        ):
            return
        self.writer_pending = None
        relevant = self.relevant(pending, now)
        try:
            result = pending.future.result()
        except Exception as exc:
            if not relevant:
                return
            if (
                pending.forced
                and pending.stage == "writer"
                and isinstance(exc, (ModelOutputError, ValidationError))
            ):
                self.emit(
                    "Writer output rejected; using a testing-mode line without another model call."
                )
                result = None
            else:
                self.fail(exc, pending.stage)
                return
        if not relevant:
            self.emit("Pending commentary discarded (stale, invalidated, or superseded).")
            return
        try:
            if pending.stage == "writer":
                pending.candidate = result.selected if result is not None else None
                if pending.forced and not self.candidate_valid(pending):
                    pending.candidate = Candidate(
                        remark="I'm here. This rectangle is receiving an unreasonable amount of supervision.",
                        premise="testing-mode fallback",
                        supporting_event_ids=[pending.trigger_id],
                    )
                    self.emit("Writer had no usable joke; using a testing-mode line.")
                if pending.candidate is None:
                    self.emit("Writer chose silence.")
                    return
                if not self.candidate_valid(pending):
                    self.emit("Backseat stays quiet (no grounded, non-repeated remark).")
                    return
                if self.text_only:
                    self.delivered(pending.candidate)
                else:
                    pending.future = self.writer_executor.submit(
                        self.providers.synthesize, pending.candidate.remark
                    )
                    pending.stage = "speech"
                    self.writer_pending = pending
            elif self.candidate_valid(pending):
                self.player.start(result)
                self.delivered(pending.candidate, audio=True)
        except Exception as exc:
            self.fail(exc, pending.stage)

    def schedule_writer(self, now):
        if (
            self.observe_only
            or self.writer_pending
            or self.playing_entry
            or self.closed
            or self.session.paused
            or self.trigger is None
        ):
            return
        event_id, started, forced = self.trigger
        if not forced and not self.session.can_speak(now):
            return
        trigger = self.event(event_id)
        if (
            event_id in self.attempted
            or now - started > self.session.config.stale_seconds
            or trigger is None
            or not trigger.valid
            or not trigger.active
        ):
            return
        self.attempted.add(event_id)
        context = self.session.task.context()
        context["testing_commentary"] = forced
        allowed = frozenset(event["id"] for event in context["events"])
        future = self.writer_executor.submit(
            self.providers.write, trigger.model_dump(), context, list(self.session.preferences)
        )
        self.writer_pending = Pending(
            future,
            self.session.epoch,
            started,
            "writer",
            trigger_id=event_id,
            allowed_ids=allowed,
            forced=forced,
        )
        self.emit("Writing commentary...")

    def schedule_observer(self, now):
        if (
            self.observer_pending
            or self.closed
            or not self.session.can_observe(now)
            or (self.once and self.observations_submitted > 0)
        ):
            return
        self.session.next_sample = now + self.session.config.sample_seconds
        forced = self.chatty or self.force_remark
        try:
            image = self.region.capture()
            if not forced and not changed(
                self.session.previous, thumbnail(image), self.session.config.change_threshold
            ):
                return
            if self.capture_dir:
                self.capture_dir.mkdir(parents=True, exist_ok=True)
                image.save(self.capture_dir / f"capture-{time.time_ns()}.png")
            observed_at = timestamp()
            context = self.session.task.context()
            context["captured_at"] = observed_at
            context["previous_captured_at"] = self.previous_captured_at
            future = self.observer_executor.submit(
                self.providers.observe, image, self.session.previous_image, context
            )
            self.observer_pending = Pending(
                future,
                self.session.epoch,
                now,
                "observer",
                image=image,
                observed_at=observed_at,
                forced=forced,
            )
            self.observations_submitted += 1
            self.force_remark = False
        except Exception as exc:
            if stage_capture_error(exc):
                self.pause()
                self.had_error = True
                self.emit(str(exc))
            else:
                self.fail(exc, "observer")

    def tick(self):
        now = self.clock()
        try:
            if self.playing_entry is not None:
                status = self.player.poll()
                if status is not None:
                    if status != 0:
                        self.emit("Audio playback failed; check your output device.")
                    self.stop_playback(interrupted=status != 0)
                    if self.captions:
                        self.captions.linger(CAPTION_LINGER_SECONDS)
            self.poll_observer(now)
            self.poll_writer(now)
            self.schedule_writer(now)
            self.schedule_observer(now)
        except MemoryStoreError as exc:
            self.fail(exc, "memory")
        if self.captions:
            self.captions.update()

    def recap(self):
        """Blocking end-of-session remark, run once from the main thread before close()."""
        # Discard in-flight work first so nothing else plays over or after the recap.
        self.invalidate()
        events = [
            event
            for event in self.session.task.events
            if event.valid
            and not event.uncertain
            and event.time >= self.started_at
            and event.description != CONTROL_DESCRIPTION
        ]
        if not events:
            self.emit("Recap skipped: no certain events were observed this session.")
            return
        self.emit("Writing session recap...")
        try:
            result = self.providers.recap(
                [event.model_dump() for event in events],
                self.session.task.context(),
                list(self.session.preferences),
            )
            if not result.speak:
                self.emit("Recap: nothing worth saying.")
                return
            if not set(result.supporting_event_ids) <= {event.id for event in events}:
                raise ModelOutputError("unknown event reference")
            audio = None if self.text_only else self.providers.synthesize(result.remark)
            remark = Remark(
                text=result.remark,
                premise="session recap",
                supporting_event_ids=result.supporting_event_ids,
                interrupted=audio is not None,
            )
            self.session.task.remarks.append(remark)
            self.session.task.updated = timestamp()
            self.save()
            self.emit(f"backseat: {remark.text}")
            if self.captions:
                self.captions.show(remark.text, self.region, reading_seconds(remark.text))
            if audio is None:
                return
            self.player.start(audio)
            # Ctrl+C here propagates to the CLI, whose close() stops playback.
            while (status := self.player.poll()) is None:
                if self.captions:
                    self.captions.update()
                time.sleep(0.1)
            if status == 0:
                remark.interrupted = False
                self.save()
        except Exception as exc:
            self.fail(exc, "recap")

    def close(self):
        self.closed = True
        try:
            self.invalidate()
        except MemoryStoreError as exc:
            self.emit(str(exc))
            self.had_error = True
        finally:
            for pending in (self.observer_pending, self.writer_pending):
                if pending:
                    pending.future.cancel()
            self.observer_executor.shutdown(wait=True, cancel_futures=True)
            self.writer_executor.shutdown(wait=True, cancel_futures=True)
            self.providers.close()


def reading_seconds(text):
    return max(4.0, 0.4 * len(text.split()))


def stage_capture_error(exc):
    from .capture import CaptureError

    return isinstance(exc, CaptureError)

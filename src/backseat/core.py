import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageChops
from pydantic import BaseModel, Field, model_validator


def timestamp():
    return datetime.now(UTC).isoformat()


def identifier():
    return uuid4().hex


class ModelOutputError(ValueError):
    """Only fixed, content-free reasons constructed by Backseat belong here."""


class Config(BaseModel):
    model: str = "gpt-5.4-mini"
    observer_model: str | None = None
    writer_model: str | None = None
    speech_model: str = "eleven_v4"
    sample_seconds: float = Field(default=30, ge=1)
    cooldown_seconds: float = Field(default=60, ge=0)
    change_threshold: float = Field(default=0.02, gt=0, le=1)
    image_max_edge: int = Field(default=2560, ge=256)
    api_timeout_seconds: float = Field(default=20, gt=0)
    stale_seconds: float = Field(default=60, gt=0)

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        with path.open("rb") as handle:
            return cls.model_validate(tomllib.load(handle))


class EventProposal(BaseModel):
    description: str = Field(min_length=1, max_length=600)
    evidence: str = Field(min_length=1, max_length=600)
    uncertain: bool
    noteworthy: bool


class Observation(BaseModel):
    activity: str = Field(min_length=1, max_length=400)
    summary: str = Field(min_length=1, max_length=4000)
    unresolved: list[str] = Field(max_length=10)
    events: list[EventProposal] = Field(max_length=5)
    superseded_event_ids: list[str]


class Candidate(BaseModel):
    remark: str
    premise: str = Field(min_length=1, max_length=300)
    supporting_event_ids: list[str]

    @model_validator(mode="after")
    def validate_remark(self):
        self.remark = self.remark.strip()
        if not self.remark or len(self.remark.split()) > 35:
            raise ValueError("Remarks must contain 1–35 words")
        if not self.supporting_event_ids:
            raise ValueError("A joke needs supporting events")
        return self


class Joke(BaseModel):
    speak: bool
    candidates: list[Candidate]
    selected_index: int

    @model_validator(mode="after")
    def validate_selection(self):
        if self.speak:
            if len(self.candidates) != 3 or not 0 <= self.selected_index < 3:
                raise ValueError("Choose one of three candidates")
        elif self.candidates or self.selected_index != -1:
            raise ValueError("Silence requires no candidates and selected_index=-1")
        return self

    @property
    def selected(self):
        return self.candidates[self.selected_index] if self.speak else None


class Event(BaseModel):
    id: str = Field(default_factory=identifier)
    time: str = Field(default_factory=timestamp)
    description: str
    evidence: str
    uncertain: bool = False
    noteworthy: bool = False
    active: bool = True
    valid: bool = True


class Remark(BaseModel):
    id: str = Field(default_factory=identifier)
    time: str = Field(default_factory=timestamp)
    text: str
    premise: str
    supporting_event_ids: list[str]
    interrupted: bool = False
    feedback: str = ""


class Task(BaseModel):
    id: str = Field(default_factory=identifier)
    updated: str = Field(default_factory=timestamp)
    goal: str = "desktop session"
    context_notes: list[str] = Field(default_factory=list)
    summary: str = "No observations yet."
    activity: str = ""
    unresolved: list[str] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    remarks: list[Remark] = Field(default_factory=list)

    def context(self):
        # Copies prevent a running request from seeing concurrent main-thread edits.
        return {
            "goal": self.goal,
            "context_notes": self.context_notes[-20:],
            "summary": self.summary,
            "activity": self.activity,
            "unresolved": self.unresolved,
            "events": [event.model_dump() for event in self.events if event.valid][-20:],
            "remarks": [remark.model_dump() for remark in self.remarks[-20:]],
            "now": timestamp(),
        }

    def apply(self, observation, observed_at, *, refresh_current=False):
        known = {event.id: event for event in self.events}
        for event_id in observation.superseded_event_ids:
            if event_id not in known:
                raise ModelOutputError("unknown event reference")
        for event_id in observation.superseded_event_ids:
            known[event_id].active = False
        self.activity = observation.activity
        self.summary = observation.summary
        self.unresolved = observation.unresolved
        fresh = []
        for proposal in observation.events:
            matches = [
                event
                for event in self.events[-20:]
                if (
                    event.valid
                    and event.active
                    and event.description.strip().casefold()
                    == proposal.description.strip().casefold()
                )
            ]
            if matches:
                if not refresh_current or any(event.time == observed_at for event in matches):
                    continue
                # A new run's first image verifies the scene again; keep the old
                # timestamp as history rather than presenting old facts as current.
                for event in matches:
                    event.active = False
            event = Event(time=observed_at, **proposal.model_dump())
            self.events.append(event)
            if not event.uncertain:
                fresh.append(event.id)
        self.updated = timestamp()
        return fresh


def thumbnail(image: Image.Image) -> Image.Image:
    return image.convert("L").resize((256, 144))


def changed(previous: Image.Image | None, current: Image.Image, threshold: float) -> bool:
    if previous is None:
        return True
    histogram = ImageChops.difference(previous, current).histogram()
    return sum(histogram[21:]) / (current.width * current.height) >= threshold


@dataclass
class Session:
    config: Config
    task: Task = field(default_factory=Task)
    preferences: list[str] = field(default_factory=list)
    previous: Image.Image | None = None
    previous_image: Image.Image | None = None
    paused: bool = False
    epoch: int = 0
    last_spoken: float = float("-inf")
    next_sample: float = 0

    def invalidate(self):
        self.epoch += 1
        self.previous = None
        self.previous_image = None

    def can_observe(self, now):
        return not self.paused and now >= self.next_sample

    def can_speak(self, now):
        return not self.paused and now - self.last_spoken >= self.config.cooldown_seconds


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_seconds: float = 0

    def summary(self, name):
        average = self.latency_seconds / self.requests if self.requests else 0
        return (
            f"{name}: {self.requests} requests, {self.input_tokens} input / "
            f"{self.output_tokens} output tokens, mean latency {average:.1f}s"
        )


@dataclass
class Stats:
    observer: Usage = field(default_factory=Usage)
    writer: Usage = field(default_factory=Usage)
    speech_requests: int = 0
    speech_characters: int = 0

    def summary(self):
        return (
            f"Session: {self.observer.summary('observer')}; {self.writer.summary('writer')}; "
            f"{self.speech_requests} speech requests, {self.speech_characters} characters."
        )

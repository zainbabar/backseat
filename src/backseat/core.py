import tomllib
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageChops
from pydantic import BaseModel, Field, model_validator


class Config(BaseModel):
    model: str = "gpt-5.4-mini"
    speech_model: str = "eleven_flash_v2_5"
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


class Commentary(BaseModel):
    observation: str
    speak: bool
    remark: str

    @model_validator(mode="after")
    def validate_remark(self):
        self.observation = self.observation.strip()
        self.remark = self.remark.strip()
        if not self.observation or len(self.observation) > 1200:
            raise ValueError("Observation must be nonempty and at most 1200 characters")
        if self.speak and (not self.remark or len(self.remark.split()) > 35):
            raise ValueError("Spoken remarks must contain 1–35 words")
        if not self.speak:
            self.remark = ""
        return self


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
    history: deque = field(default_factory=lambda: deque(maxlen=6))
    previous: Image.Image | None = None
    paused: bool = False
    epoch: int = 0
    last_spoken: float = float("-inf")
    next_sample: float = 0

    def invalidate(self, *, reset_history=False):
        self.epoch += 1
        self.previous = None
        if reset_history:
            self.history.clear()

    def eligible(self, now: float):
        return (
            not self.paused
            and now >= self.next_sample
            and now - self.last_spoken >= self.config.cooldown_seconds
        )

    def record(self, commentary: Commentary):
        entry = {
            "time": datetime.now(UTC).isoformat(),
            "observation": commentary.observation,
            "remark": "",
        }
        self.history.append(entry)
        return entry


@dataclass
class Stats:
    requests: int = 0
    speech_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    speech_characters: int = 0
    latency_seconds: float = 0

    def summary(self):
        average = self.latency_seconds / self.requests if self.requests else 0
        return (
            f"Session: {self.requests} model requests, {self.input_tokens} input / "
            f"{self.output_tokens} output tokens; {self.speech_requests} speech requests, "
            f"{self.speech_characters} characters; mean model latency {average:.1f}s."
        )

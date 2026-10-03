from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from backseat.core import Commentary, Config, Stats
from backseat.providers import Providers


def test_model_payload_and_usage_are_correct():
    stats = Stats()
    provider = Providers(Config(), stats, "test-key")
    provider.model = Mock()
    expected = Commentary(observation="A test failure", speak=False, remark="")
    provider.model.responses.parse.return_value = SimpleNamespace(
        output_parsed=expected, usage=SimpleNamespace(input_tokens=1000, output_tokens=50)
    )
    assert provider.observe(Image.new("RGB", (5120, 1440)), []) == expected
    payload = provider.model.responses.parse.call_args.kwargs
    assert payload["store"] is False
    assert payload["reasoning"] == {"effort": "none"}
    assert payload["input"][1]["content"][1]["image_url"].startswith("data:image/png;base64,")
    assert stats.input_tokens == 1000
    assert stats.output_tokens == 50


def test_refused_output_is_rejected_without_speech():
    provider = Providers(Config(), Stats(), "test-key")
    provider.model = Mock()
    provider.model.responses.parse.return_value = SimpleNamespace(output_parsed=None, usage=None)
    with pytest.raises(ValueError, match="no valid commentary"):
        provider.observe(Image.new("RGB", (100, 100)), [])


def test_speech_uses_supplied_voice_and_tracks_requested_characters():
    stats = Stats()
    provider = Providers(Config(), stats, "test-key", voice_id="my-voice")
    provider.speech = Mock()
    provider.speech.text_to_speech.convert.return_value = iter([b"one", b"two"])
    assert provider.synthesize("Nice code.") == b"onetwo"
    assert provider.speech.text_to_speech.convert.call_args.kwargs["voice_id"] == "my-voice"
    assert stats.speech_characters == 10

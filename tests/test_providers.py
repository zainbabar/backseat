from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from backseat.core import Config, Joke, ModelOutputError, Observation, Stats
from backseat.providers import Providers, WriterResponse


@pytest.fixture
def provider():
    provider = Providers(
        Config(observer_model="vision", writer_model="comedy"), Stats(), "test-key"
    )
    provider.model.close()
    provider.model = Mock()
    yield provider
    provider.close()


def result(parsed):
    return SimpleNamespace(
        output_parsed=parsed, usage=SimpleNamespace(input_tokens=1000, output_tokens=50)
    )


def test_observer_gets_two_images_and_writer_gets_only_text(provider):
    observation = Observation(
        activity="test passed",
        summary="Failure resolved",
        unresolved=[],
        events=[],
        superseded_event_ids=[],
    )
    provider.model.responses.parse.return_value = result(observation)
    image = Image.new("RGB", (5120, 1440))
    assert provider.observe(image, image, {"goal": "practice"}) == observation
    payload = provider.model.responses.parse.call_args.kwargs
    assert payload["store"] is False
    assert payload["reasoning"] == {"effort": "none"}
    assert payload["model"] == "vision"
    assert len([p for p in payload["input"][1]["content"] if p["type"] == "input_image"]) == 2
    joke = Joke(speak=False, candidates=[], selected_index=-1)
    provider.model.responses.parse.return_value = result(joke)
    assert provider.write({"id": "e1"}, {}, ["dry humor"]) == joke
    payload = provider.model.responses.parse.call_args.kwargs
    assert payload["model"] == "comedy"
    assert all(p["type"] == "input_text" for p in payload["input"][1]["content"])
    assert provider.stats.observer.input_tokens == provider.stats.writer.input_tokens == 1000
    assert provider.stats.observer.requests == provider.stats.writer.requests == 1


def test_first_observation_has_one_image_and_refusal_is_rejected(provider):
    provider.model.responses.parse.return_value = SimpleNamespace(output_parsed=None, usage=None)
    with pytest.raises(ValueError, match="No valid structured"):
        provider.observe(Image.new("RGB", (100, 100)), None, {})
    payload = provider.model.responses.parse.call_args.kwargs
    assert len([p for p in payload["input"][1]["content"] if p["type"] == "input_image"]) == 1
    assert provider.stats.observer.requests == 1


def test_testing_mode_explicitly_requests_speech_without_losing_grounding(provider):
    provider.model.responses.parse.return_value = result(
        Joke(speak=False, candidates=[], selected_index=-1)
    )
    provider.write({"id": "request"}, {"testing_commentary": True}, [])
    prompt = provider.model.responses.parse.call_args.kwargs["input"][0]["content"]
    assert "Set speak=true" in prompt
    assert "respect uncertainty" in prompt
    assert "Never quote secrets/private text" in prompt


def test_speech_uses_supplied_voice_and_tracks_requested_characters(provider):
    provider.voice_id = "my-voice"
    provider.speech = Mock()
    provider.speech.text_to_speech.convert.return_value = iter([b"one", b"two"])
    assert provider.synthesize("Nice code.") == b"onetwo"
    assert provider.speech.text_to_speech.convert.call_args.kwargs["voice_id"] == "my-voice"
    assert provider.stats.speech_characters == 10


@pytest.mark.parametrize("stage", ["observer", "writer"])
def test_real_sdk_serializes_schema_and_parses_mock_http_response(provider, stage):
    import json

    import httpx
    from openai import OpenAI

    observation = Observation(
        activity="tests passing",
        summary="Observed FAIL then PASS",
        unresolved=[],
        events=[],
        superseded_event_ids=[],
    )
    output = observation
    if stage == "writer":
        output = WriterResponse.model_validate(
            {
                "speak": True,
                "selected_index": 0,
                "candidates": [
                    {
                        "remark": "The test passed. I'll notify the historical society.",
                        "premise": "payoff",
                        "supporting_event_ids": ["e1"],
                    },
                    {"remark": "word " * 36, "premise": "overlong", "supporting_event_ids": ["e1"]},
                    {"remark": "word " * 40, "premise": "overlong", "supporting_event_ids": ["e1"]},
                ],
            }
        )
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "gpt-5.4-mini",
                "parallel_tool_calls": False,
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": output.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "tools": [],
                "metadata": {},
                "usage": {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12},
            },
        )

    provider.model = OpenAI(
        api_key="test",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        max_retries=0,
    )
    if stage == "observer":
        assert provider.observe(Image.new("RGB", (100, 100)), None, {}) == observation
    else:
        joke = provider.write({"id": "e1"}, {}, [])
        assert joke.selected.remark == output.candidates[0].remark
    assert requests[0]["text"]["format"]["type"] == "json_schema"
    assert requests[0]["text"]["format"]["strict"] is True
    assert requests[0]["store"] is False
    assert getattr(provider.stats, stage).input_tokens == 5


@pytest.mark.parametrize("selected_index", [0, 1])
def test_writer_preserves_short_joke_when_another_candidate_is_overlong(provider, selected_index):
    payload = WriterResponse.model_validate(
        {
            "speak": True,
            "selected_index": selected_index,
            "candidates": [
                {"remark": "word " * 36, "premise": "long", "supporting_event_ids": ["e1"]},
                {
                    "remark": "This function has achieved work-life balance.",
                    "premise": "empty function",
                    "supporting_event_ids": ["e1"],
                },
                {"remark": "word " * 40, "premise": "long", "supporting_event_ids": ["e1"]},
            ],
        }
    )
    provider.model.responses.parse.return_value = result(payload)
    joke = provider.write({"id": "e1"}, {}, [])
    assert joke.selected.remark == "This function has achieved work-life balance."
    assert all(len(candidate.remark.split()) <= 35 for candidate in joke.candidates)
    assert provider.stats.writer.input_tokens == 1000
    assert provider.model.responses.parse.call_count == 1


def test_writer_rejects_all_invalid_candidates_without_exposing_text(provider):
    provider.model.responses.parse.return_value = result(
        WriterResponse.model_validate(
            {
                "speak": True,
                "selected_index": 0,
                "candidates": [
                    {
                        "remark": "PRIVATE " * 36,
                        "premise": "private",
                        "supporting_event_ids": ["e1"],
                    }
                ]
                * 3,
            }
        )
    )
    with pytest.raises(ModelOutputError, match="1-35 word") as caught:
        provider.write({"id": "e1"}, {}, [])
    assert "PRIVATE" not in str(caught.value)


def test_sdk_validation_failure_becomes_content_free_output_error(provider):
    from pydantic import ValidationError

    try:
        WriterResponse.model_validate({"speak": "PRIVATE"})
    except ValidationError as exc:
        provider.model.responses.parse.side_effect = exc
    with pytest.raises(ModelOutputError, match="required structure") as caught:
        provider.write({"id": "e1"}, {}, [])
    assert "PRIVATE" not in str(caught.value)


def test_persona_changes_voice_but_keeps_commentary_rules(provider):
    from backseat.providers import PERSONAS

    provider.config = Config(persona="documentary")
    provider.model.responses.parse.return_value = result(
        Joke(speak=False, candidates=[], selected_index=-1)
    )
    provider.write({"id": "e1"}, {}, [])
    prompt = provider.model.responses.parse.call_args.kwargs["input"][0]["content"]
    assert PERSONAS["documentary"] in prompt
    assert PERSONAS["friend"] not in prompt
    assert "No coding advice" in prompt
    assert "Never quote secrets/private text" in prompt


def test_every_config_persona_has_a_prompt():
    from typing import get_args

    from backseat.core import Persona
    from backseat.providers import PERSONAS

    assert set(PERSONAS) == set(get_args(Persona))


def test_recap_is_a_text_only_writer_request(provider):
    from backseat.core import Recap

    recap = Recap(speak=True, remark="Quite a session.", supporting_event_ids=["e1"])
    provider.model.responses.parse.return_value = result(recap)
    assert provider.recap([{"id": "e1"}], {"goal": "practice"}, ["dry"]) == recap
    payload = provider.model.responses.parse.call_args.kwargs
    assert payload["model"] == "comedy"
    assert payload["text_format"] is Recap
    assert all(p["type"] == "input_text" for p in payload["input"][1]["content"])
    assert "closing recap" in payload["input"][0]["content"]
    assert provider.stats.writer.requests == 1


def test_local_voice_keeps_text_out_of_arguments(provider, monkeypatch):
    import subprocess

    from backseat import providers as module

    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        output = command[command.index("-o") + 1]
        with open(output, "wb") as handle:
            handle.write(b"FORMaudio")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    provider.config = Config(say_voice="Samantha", api_timeout_seconds=7)
    assert provider.speech is None
    assert provider.synthesize("Secret-free remark.") == b"FORMaudio"
    command, kwargs = calls[0]
    assert command[0] == "say" and command[-2:] == ["-v", "Samantha"]
    assert "Secret-free remark." not in " ".join(command)
    assert kwargs["input"] == b"Secret-free remark."
    assert kwargs["timeout"] == 7 and kwargs["check"] is True
    assert provider.stats.speech_requests == 1

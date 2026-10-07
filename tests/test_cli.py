import sys

import pytest
from PIL import Image

from backseat import cli
from backseat.core import Candidate, EventProposal, Joke, Observation


class Providers:
    def __init__(self, *args, **kwargs):
        pass

    def observe(self, image, previous, context):
        return Observation(
            activity="tests passing",
            summary="Tests passed",
            unresolved=[],
            events=[
                EventProposal(
                    description="Tests passed", evidence="PASS", uncertain=False, noteworthy=True
                )
            ],
            superseded_event_ids=[],
        )

    def write(self, trigger, context, preferences):
        candidate = Candidate(
            remark="The test passed. I'll notify the historical society.",
            premise="exaggerated celebration",
            supporting_event_ids=[trigger["id"]],
        )
        return Joke(speak=True, candidates=[candidate] * 3, selected_index=0)

    def close(self):
        pass


class Region:
    def capture(self):
        return Image.new("RGB", (100, 100))


def test_isolated_cli_runs_observer_and_writer_without_saving_memory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(sys, "argv", ["backseat", "once", "--text-only", "--monitor", "1"])
    monkeypatch.setattr(cli, "check_permission", lambda: None)
    monkeypatch.setattr(cli, "choose_monitor", lambda number: {})
    monkeypatch.setattr(cli, "select_region", lambda *args: Region())
    monkeypatch.setattr(cli, "Providers", Providers)
    assert cli.main() == 0
    output = capsys.readouterr().out
    assert "saw: tests passing" in output
    assert "historical society" in output
    assert not (tmp_path / ".backseat").exists()


def test_preview_does_not_open_memory_or_providers(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["backseat", "preview", "--monitor", "1"])
    monkeypatch.setattr(cli, "check_permission", lambda: None)
    monkeypatch.setattr(cli, "choose_monitor", lambda number: {})
    monkeypatch.setattr(cli, "select_region", lambda *args: Region())

    def unexpected(*args, **kwargs):
        raise AssertionError("Preview must not construct memory/providers")

    monkeypatch.setattr(cli, "MemoryStore", unexpected)
    monkeypatch.setattr(cli, "Providers", unexpected)
    assert cli.main() == 0
    assert "No API calls" in capsys.readouterr().out
    assert not (tmp_path / ".backseat").exists()


def test_interval_overrides_run_without_modifying_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "backseat.toml"
    config.write_text("sample_seconds = 30\ncooldown_seconds = 60\n")
    original = config.read_text()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(sys, "argv", ["backseat", "once", "--text-only", "--interval", "10"])
    monkeypatch.setattr(cli, "check_permission", lambda: None)
    monkeypatch.setattr(cli, "choose_monitor", lambda number: {})
    monkeypatch.setattr(cli, "select_region", lambda *args: Region())
    received = []

    def providers(settings, *args, **kwargs):
        received.append(settings)
        return Providers()

    monkeypatch.setattr(cli, "Providers", providers)
    assert cli.main() == 0
    assert received[0].sample_seconds == received[0].cooldown_seconds == 10
    assert config.read_text() == original


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "nope"])
def test_invalid_interval_is_rejected(value):
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["run", "--interval", value])


@pytest.mark.parametrize("flag", ["--force-remark", "--chatty"])
def test_cli_forced_once_works_without_observer_events(tmp_path, monkeypatch, capsys, flag):
    class SilentProviders(Providers):
        def observe(self, *args):
            result = super().observe(*args)
            result.events = []
            return result

        def write(self, trigger, context, preferences):
            assert context["testing_commentary"]
            return Joke(speak=False, candidates=[], selected_index=-1)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(sys, "argv", ["backseat", "once", "--text-only", flag])
    monkeypatch.setattr(cli, "check_permission", lambda: None)
    monkeypatch.setattr(cli, "choose_monitor", lambda number: {})
    monkeypatch.setattr(cli, "select_region", lambda *args: Region())
    monkeypatch.setattr(cli, "Providers", SilentProviders)
    assert cli.main() == 0
    assert "supervision" in capsys.readouterr().out
    assert not (tmp_path / ".backseat").exists()

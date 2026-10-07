import sqlite3
from argparse import Namespace

import pytest

from backseat.cli import choose_task, read_commands
from backseat.core import Event, Remark, Task
from backseat.memory import MemoryError, MemoryStore


def options(**overrides):
    values = dict(command="run", resume=False, fresh=False, task=None)
    values.update(overrides)
    return Namespace(**values)


def test_restart_resume_and_fresh_preserve_only_explicit_preferences(tmp_path):
    path = tmp_path / "memory.sqlite"
    store = MemoryStore(path)
    task = Task(
        goal="binary search practice",
        summary="One failing case",
        events=[Event(description="test failed", evidence="FAIL")],
        remarks=[
            Remark(
                text="Your test is unimpressed.", premise="failing test", supporting_event_ids=[]
            )
        ],
    )
    store.save(task, ["dry jokes"])
    store.close()
    store = MemoryStore(path)
    resumed = choose_task(store, options(resume=True), interactive=False)
    assert resumed.id == task.id
    assert resumed.events[0].time == task.events[0].time
    fresh = choose_task(store, options(fresh=True, task="new project"), interactive=False)
    assert fresh.id != task.id and not fresh.events and not fresh.remarks
    assert store.preferences() == ["dry jokes"]
    store.close()


def test_once_reads_preferences_without_writing_task_history(tmp_path):
    path = tmp_path / "memory.sqlite"
    store = MemoryStore(path)
    task = Task(goal="real task")
    store.save(task, ["no stock openings"])
    store.close()
    store = MemoryStore(path, readonly=True)
    isolated = choose_task(store, options(command="once"), interactive=False)
    store.save(isolated, ["ignored"])
    assert store.last_task().id == task.id
    assert store.preferences() == ["no stock openings"]
    store.close()


def test_readonly_missing_store_does_not_create_a_file(tmp_path):
    path = tmp_path / "missing" / "memory.sqlite"
    store = MemoryStore(path, readonly=True)
    assert store.preferences() == [] and store.last_task() is None
    store.close()
    assert not path.parent.exists()


def test_unreadable_or_future_version_memory_is_not_replaced(tmp_path):
    path = tmp_path / "memory.sqlite"
    path.write_bytes(b"this is not a database")
    with pytest.raises(MemoryError):
        MemoryStore(path)
    assert path.read_bytes() == b"this is not a database"
    path.unlink()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=42")
    with pytest.raises(MemoryError, match="unsupported version"):
        MemoryStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 42


def test_noninteractive_requires_explicit_memory_choice(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite")
    with pytest.raises(ValueError, match="--resume or --fresh"):
        choose_task(store, options(), interactive=False)
    with pytest.raises(ValueError, match="--fresh --task"):
        choose_task(store, options(resume=True, task="new task"), interactive=False)
    store.close()


def test_interactive_choice_happens_before_command_reader(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / "memory.sqlite")
    saved = Task(goal="last task")
    store.save(saved, [])
    choices = iter(["invalid", "resume"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(choices))
    assert choose_task(store, options(), interactive=True).id == saved.id
    store.close()


def test_command_reader_preserves_context_case(monkeypatch):
    import io
    import queue

    monkeypatch.setattr("sys.stdin", io.StringIO("context I'm working on PathMNIST\n"))
    commands = queue.Queue()
    read_commands(commands)
    assert commands.get() == "context I'm working on PathMNIST"
    assert commands.get() == "quit"

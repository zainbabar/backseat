import json
import sqlite3
from pathlib import Path

from .core import Task


class MemoryStoreError(RuntimeError):
    pass


class MemoryStore:
    """Main-thread SQLite writes; snapshots are validated before use."""

    def __init__(self, path=Path(".backseat/memory.sqlite"), *, readonly=False):
        self.readonly = readonly
        try:
            if readonly and path.exists():
                self.db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            elif readonly:
                self.db = sqlite3.connect(":memory:")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                self.db = sqlite3.connect(path)
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise MemoryStoreError("Memory has an unsupported version; it was not overwritten.")
            if not readonly or not path.exists():
                with self.db:
                    self.db.execute(
                        "CREATE TABLE IF NOT EXISTS tasks "
                        "(id TEXT PRIMARY KEY, updated TEXT NOT NULL, payload TEXT NOT NULL)"
                    )
                    self.db.execute(
                        "CREATE TABLE IF NOT EXISTS preferences "
                        "(id INTEGER PRIMARY KEY CHECK (id=1), payload TEXT NOT NULL)"
                    )
                    self.db.execute("PRAGMA user_version=1")
            self.last_task()  # Catch unreadable data before starting a run.
            self.preferences()
        except MemoryStoreError:
            if hasattr(self, "db"):
                self.db.close()
            raise
        except Exception as exc:
            if hasattr(self, "db"):
                self.db.close()
            raise MemoryStoreError(
                "Could not open or read local memory. Check .backseat/memory.sqlite; "
                "the existing file was not replaced."
            ) from exc

    def last_task(self):
        try:
            row = self.db.execute(
                "SELECT payload FROM tasks ORDER BY updated DESC LIMIT 1"
            ).fetchone()
            return Task.model_validate_json(row[0]) if row else None
        except Exception as exc:
            raise MemoryStoreError(
                "Saved task memory is unreadable; it was not overwritten."
            ) from exc

    def preferences(self):
        try:
            row = self.db.execute("SELECT payload FROM preferences WHERE id=1").fetchone()
            notes = json.loads(row[0]) if row else []
            if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
                raise ValueError("Invalid preferences")
            return notes[-20:]
        except Exception as exc:
            raise MemoryStoreError("Saved humor preferences are unreadable.") from exc

    def save(self, task, preferences):
        if self.readonly:
            return
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO tasks VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                    "updated=excluded.updated, payload=excluded.payload",
                    (task.id, task.updated, task.model_dump_json()),
                )
                self.db.execute(
                    "INSERT INTO preferences VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET "
                    "payload=excluded.payload",
                    (json.dumps(preferences[-20:]),),
                )
        except sqlite3.Error as exc:
            raise MemoryStoreError(
                "Could not save task memory. Check disk space and permissions; "
                "Backseat is paused to avoid silently losing context."
            ) from exc

    def close(self):
        self.db.close()

"""Persistent registry of active faults (state/active_faults.json) with an inter-process lock.

Both the orchestrator and the independent watchdog read it, so either can roll back.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class Registry:
    def __init__(self, state_dir: Path):
        self.dir = state_dir
        self.path = state_dir / "active_faults.json"
        self.lock_path = state_dir / "registry.lock"
        self.dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self.lock_path.open("a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text() or "[]")
        except json.JSONDecodeError:
            return []

    def _write(self, entries: list[dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, indent=2))
        os.replace(tmp, self.path)

    def add(self, entry: dict[str, Any]) -> None:
        with self._locked():
            entries = [e for e in self._read() if e["fault_id"] != entry["fault_id"]]
            entries.append(entry)
            self._write(entries)

    def update(self, fault_id: str, **fields: Any) -> None:
        with self._locked():
            entries = self._read()
            for e in entries:
                if e["fault_id"] == fault_id:
                    e.update(fields)
            self._write(entries)

    def remove(self, fault_id: str) -> None:
        with self._locked():
            self._write([e for e in self._read() if e["fault_id"] != fault_id])

    def list(self, run_id: str | None = None) -> list[dict[str, Any]]:
        with self._locked():
            entries = self._read()
        return [e for e in entries if run_id is None or e.get("run_id") == run_id]

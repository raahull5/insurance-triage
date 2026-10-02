import json
from pathlib import Path

import pytest

from src.ingestion.retry_queue import RetryQueue


def test_queue_survives_restart(tmp_path):
    path = tmp_path / "retry_queue.json"

    first = RetryQueue(path)
    first.add("101")
    first.add("102")

    second = RetryQueue(path)
    assert second.pending == ["101", "102"]


def test_queue_deduplicates_ids(tmp_path):
    queue = RetryQueue(tmp_path / "retry_queue.json")

    queue.add("101")
    queue.add("101")

    assert queue.pending == ["101"]


def test_queue_removes_resolved_id(tmp_path):
    path = tmp_path / "retry_queue.json"
    queue = RetryQueue(path)

    queue.add("101")
    queue.add("102")
    queue.remove("101")

    assert queue.pending == ["102"]
    assert RetryQueue(path).pending == ["102"]


def test_invalid_queue_file_raises(tmp_path):
    path = tmp_path / "retry_queue.json"
    path.write_text('{"invalid": true}', encoding="utf-8")

    with pytest.raises(ValueError):
        RetryQueue(path)


def test_persistence_failure_keeps_memory_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "retry_queue.json"
    queue = RetryQueue(path)
    queue.add("101")

    def fail_replace(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError):
        queue.add("102")

    assert queue.pending == ["101"]
    assert json.loads(path.read_text(encoding="utf-8")) == ["101"]

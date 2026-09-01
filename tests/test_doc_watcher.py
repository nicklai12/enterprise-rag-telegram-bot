"""Tests for doc_watcher.py."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

import doc_watcher


def _make_queue_and_files(
    tmp_path: pathlib.Path,
    count: int,
    decision: str = "auto_process",
    prefix: str = "doc",
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path, list[dict[str, str]]]:
    """Create ``count`` fake raw files and a matching pending queue."""
    raw_dir = tmp_path / "data" / "raw"
    (raw_dir / "HR").mkdir(parents=True, exist_ok=True)
    queue: list[dict[str, str]] = []
    for i in range(count):
        source_file = f"data/raw/HR/{prefix}_{i:02d}.txt"
        file_path = tmp_path / source_file
        file_path.write_text(f"content {i}", encoding="utf-8")
        queue.append(
            {
                "doc_id": f"HR_{prefix}_{i:02d}",
                "source_file": source_file,
                "department": "HR",
                "decision": decision,
            }
        )

    pending_path = tmp_path / "pending_queue.json"
    pending_path.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

    state_path = tmp_path / "status" / "watcher_state.json"
    return pending_path, state_path, raw_dir, queue


def test_only_auto_items_are_selected(tmp_path: pathlib.Path):
    """Items with decision != auto_process are ignored."""
    raw_dir = tmp_path / "data" / "raw"
    (raw_dir / "HR").mkdir(parents=True, exist_ok=True)

    auto_file = "data/raw/HR/auto.txt"
    review_file = "data/raw/HR/review.txt"
    (tmp_path / auto_file).write_text("auto", encoding="utf-8")
    (tmp_path / review_file).write_text("review", encoding="utf-8")

    queue = [
        {
            "doc_id": "HR_auto",
            "source_file": auto_file,
            "department": "HR",
            "decision": "auto_process",
        },
        {
            "doc_id": "HR_review",
            "source_file": review_file,
            "department": "HR",
            "decision": "needs_review",
        },
    ]
    pending_path = tmp_path / "pending_queue.json"
    pending_path.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")

    batch = doc_watcher.run(
        pending_queue_path=pending_path,
        state_path=tmp_path / "watcher_state.json",
        raw_dir=raw_dir,
    )

    assert batch == [auto_file]


def test_batch_capped_at_twenty_and_remaining_next_run(tmp_path: pathlib.Path):
    """25 new files produce 20 then 5; state records all 25 hashes."""
    pending_path, state_path, raw_dir, queue = _make_queue_and_files(
        tmp_path, count=25, prefix="doc"
    )

    first_batch = doc_watcher.run(
        pending_queue_path=pending_path,
        state_path=state_path,
        raw_dir=raw_dir,
    )

    expected_first = [item["source_file"] for item in queue[:20]]
    assert first_batch == expected_first
    assert len(first_batch) == 20

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state["files"]) == 25
    processed = [k for k, v in state["files"].items() if v.get("last_processed_at")]
    assert len(processed) == 20

    second_batch = doc_watcher.run(
        pending_queue_path=pending_path,
        state_path=state_path,
        raw_dir=raw_dir,
    )

    expected_second = [item["source_file"] for item in queue[20:]]
    assert second_batch == expected_second
    assert len(second_batch) == 5



def test_processed_unchanged_files_not_reoutput(tmp_path: pathlib.Path):
    """Once processed, unchanged files are no longer emitted."""
    pending_path, state_path, raw_dir, queue = _make_queue_and_files(
        tmp_path, count=3, prefix="stable"
    )

    first = doc_watcher.run(
        pending_queue_path=pending_path,
        state_path=state_path,
        raw_dir=raw_dir,
    )
    assert len(first) == 3

    second = doc_watcher.run(
        pending_queue_path=pending_path,
        state_path=state_path,
        raw_dir=raw_dir,
    )
    assert second == []


def test_changed_file_is_reprocessed(tmp_path: pathlib.Path):
    """A file modified after processing is emitted again."""
    pending_path, state_path, raw_dir, queue = _make_queue_and_files(
        tmp_path, count=2, prefix="mutable"
    )

    first = doc_watcher.run(
        pending_queue_path=pending_path,
        state_path=state_path,
        raw_dir=raw_dir,
    )
    assert len(first) == 2

    # Modify one file.
    target = queue[0]["source_file"]
    (tmp_path / target).write_text("changed content", encoding="utf-8")

    second = doc_watcher.run(
        pending_queue_path=pending_path,
        state_path=state_path,
        raw_dir=raw_dir,
    )
    assert second == [target]


def test_run_returns_source_file_paths(tmp_path: pathlib.Path):
    """The returned batch is a list of repo-relative source-file paths."""
    pending_path, state_path, raw_dir, queue = _make_queue_and_files(
        tmp_path, count=1, prefix="single"
    )

    batch = doc_watcher.run(
        pending_queue_path=pending_path,
        state_path=state_path,
        raw_dir=raw_dir,
    )

    assert isinstance(batch, list)
    assert all(isinstance(p, str) for p in batch)
    assert batch == [queue[0]["source_file"]]

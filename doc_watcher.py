"""doc_watcher.py: detect new/changed auto-process documents with a 20-doc cap.

Reads ``pending_queue.json`` (classification == "auto_process"), compares each
file against the last recorded hash in ``status/watcher_state.json``, and emits
a deterministic batch of at most 20 source-file paths. Unselected files stay in
the queue for the next run; already processed and unchanged files are skipped.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent
PENDING_QUEUE_PATH = ROOT / "pending_queue.json"
STATE_PATH = ROOT / "status" / "watcher_state.json"
RAW_DIR = ROOT / "data" / "raw"
BATCH_SIZE = 20


def load_pending_queue(path: pathlib.Path = PENDING_QUEUE_PATH) -> list[dict[str, Any]]:
    """Load the pending queue produced by ``doc_classifier.py``."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_state(path: pathlib.Path = STATE_PATH) -> dict[str, Any]:
    """Load the watcher state; return an empty state if it does not exist."""
    if not path.exists():
        return {"files": {}}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict[str, Any], path: pathlib.Path = STATE_PATH) -> None:
    """Persist the watcher state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def file_hash(path: pathlib.Path) -> str | None:
    """Return the hex md5 digest of a file, or ``None`` if the file is absent."""
    if not path.exists():
        return None
    hasher = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _is_pending(
    entry: dict[str, Any],
    current_hash: str | None,
) -> bool:
    """Return True if the file is new, changed, or previously left unprocessed."""
    if not entry:
        return True
    if entry.get("hash") != current_hash:
        return True
    if entry.get("last_processed_at") is None:
        return True
    return False


def select_batch(
    queue: list[dict[str, Any]],
    state: dict[str, Any],
    raw_dir: pathlib.Path = RAW_DIR,
    batch_size: int = BATCH_SIZE,
) -> tuple[list[str], dict[str, Any]]:
    """Select up to ``batch_size`` source files that need processing.

    Returns the batch (sorted repo-relative paths) and the updated state.
    """
    state_files: dict[str, Any] = state.get("files", {})
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    pending: list[dict[str, Any]] = []
    for item in queue:
        if item.get("decision") != "auto_process":
            continue

        source_file = item["source_file"]
        doc_id = item["doc_id"]
        abs_path = raw_dir.parent.parent / source_file
        current_hash = file_hash(abs_path)

        old_entry = state_files.get(doc_id, {})
        if _is_pending(old_entry, current_hash):
            pending.append(item)

        state_files.setdefault(doc_id, {})
        state_files[doc_id]["hash"] = current_hash

    pending.sort(key=lambda item: item["source_file"])
    batch = pending[:batch_size]

    for item in batch:
        doc_id = item["doc_id"]
        state_files[doc_id]["last_processed_at"] = now

    state["last_run"] = now
    state["files"] = state_files
    return [item["source_file"] for item in batch], state


def run(
    pending_queue_path: pathlib.Path = PENDING_QUEUE_PATH,
    state_path: pathlib.Path = STATE_PATH,
    raw_dir: pathlib.Path = RAW_DIR,
    batch_size: int = BATCH_SIZE,
) -> list[str]:
    """Execute the watcher and return the current batch of source-file paths."""
    queue = load_pending_queue(pending_queue_path)
    state = load_state(state_path)
    batch, updated_state = select_batch(queue, state, raw_dir, batch_size)
    save_state(updated_state, state_path)
    return batch


if __name__ == "__main__":
    batch = run()
    print(json.dumps(batch, ensure_ascii=False, indent=2))

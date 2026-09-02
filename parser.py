"""parser.py: parse a single raw document with Unstructured into elements.

Usage: ``python parser.py <source_file>``

Reads one file (PDF / Word / Excel / text) from ``data/raw/``, parses it with
``unstructured.partition.auto``, and writes ``data/parsed/{doc_id}.json`` plus
``data/parsed/{doc_id}.manifest.json``. A parse failure for the single document
is recorded in the manifest (``status: failed`` + ``parse_errors``); the
process always exits 0 so a batch caller is never interrupted.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import sys
from typing import Any

from unstructured.partition.auto import partition

import doc_classifier

ROOT = pathlib.Path(__file__).resolve().parent
PENDING_QUEUE_PATH = ROOT / "pending_queue.json"
PARSED_DIR = ROOT / "data" / "parsed"


def load_pending_queue(path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    """Load the pending queue; return an empty list if it does not exist."""
    path = path or PENDING_QUEUE_PATH
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_doc_info(source_file: str) -> tuple[str, str]:
    """Resolve ``(doc_id, department)`` for a repo-relative source path.

    The pending queue (from ``doc_classifier.py``) is authoritative when the
    file is listed there; otherwise derive both with the classifier's rules.
    """
    queue = load_pending_queue()
    for item in queue:
        if item.get("source_file") == source_file:
            return item["doc_id"], item.get("department", "unknown")

    config = doc_classifier.load_config()
    doc_id = doc_classifier._doc_id(source_file)
    department = doc_classifier._department(source_file, config.get("watch_folders", []))
    return doc_id, department


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Keep only JSON-serializable metadata values."""
    clean: dict[str, Any] = {}
    for key, value in metadata.items():
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        clean[key] = value
    return clean


def parse_document(
    source_file: str,
    doc_id: str,
    department: str,
    raw_root: pathlib.Path = ROOT,
    parsed_dir: pathlib.Path = PARSED_DIR,
) -> dict[str, Any]:
    """Parse one document and write parsed json + manifest.

    Returns the manifest dict. On any parse error the manifest records
    ``status: failed`` with the error message in ``parse_errors``; no
    exception propagates to the caller.
    """
    parsed_dir.mkdir(parents=True, exist_ok=True)
    abs_path = raw_root / source_file

    manifest: dict[str, Any] = {
        "doc_id": doc_id,
        "source_file": source_file,
        "department": department,
        "status": "failed",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "parse_errors": [],
        "stage": "parser",
    }

    try:
        if not abs_path.is_file():
            raise FileNotFoundError(f"找不到原始文件：{source_file}")
        elements = [
            {
                "type": el.category,
                "text": el.text,
                "metadata": _sanitize_metadata(el.metadata.to_dict()),
            }
            for el in partition(str(abs_path))
        ]
        parsed = {
            "doc_id": doc_id,
            "source_file": source_file,
            "department": department,
            "elements": elements,
        }
        with (parsed_dir / f"{doc_id}.json").open("w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)
        manifest["status"] = "success"
    except Exception as exc:  # noqa: BLE001 - single-doc failure must not crash the batch
        manifest["parse_errors"] = [str(exc)]

    with (parsed_dir / f"{doc_id}.manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return manifest


def run(source_file: str) -> dict[str, Any]:
    """Parse one repo-relative source file and write its outputs."""
    doc_id, department = resolve_doc_info(source_file)
    return parse_document(source_file, doc_id, department)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python parser.py <source_file>", file=sys.stderr)
        sys.exit(2)
    manifest = run(sys.argv[1])
    print(json.dumps(manifest, ensure_ascii=False, indent=2))

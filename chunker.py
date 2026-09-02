"""chunker.py: hierarchical chunking for parsed documents.

Reads a parsed document (``data/parsed/{doc_id}.json`` in production,
``data/fixtures/sample_parsed.json`` for development), groups its elements
into size-limited chunks according to ``config/pipeline.yaml:chunking``
(strategy / chunk_size / overlap), and writes ``data/chunks/{doc_id}.json``
plus a ``{doc_id}.manifest.json`` hand-off manifest.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "pipeline.yaml"
FIXTURE_PATH = ROOT / "data" / "fixtures" / "sample_parsed.json"
CHUNKS_DIR = ROOT / "data" / "chunks"


def load_config(path: pathlib.Path = CONFIG_PATH) -> dict[str, Any]:
    """Load pipeline configuration from YAML."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _split_long_text(text: str, chunk_size: int, overlap_chars: int) -> list[str]:
    """Hard-split a single over-sized element by characters, with overlap."""
    step = max(1, chunk_size - overlap_chars)
    pieces: list[str] = []
    start = 0
    while start < len(text):
        pieces.append(text[start : start + chunk_size])
        start += step
    return pieces


def chunk_elements(
    elements: list[dict[str, Any]],
    chunk_size: int,
    overlap: float,
) -> list[dict[str, Any]]:
    """Group parsed elements into hierarchical chunks.

    Hierarchical here means element/paragraph boundaries are respected:
    consecutive elements are merged greedily while the total stays within
    ``chunk_size`` characters, and each chunk records the nearest preceding
    Title as its section context. Elements longer than ``chunk_size`` are
    hard-split by characters with ``overlap`` (a fraction of ``chunk_size``)
    repeated between pieces; the same overlap tail is carried from a closed
    chunk into the next one.
    """
    overlap_chars = int(chunk_size * overlap)
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    current_len = 0
    prev_tail = ""
    section: str | None = None

    def flush() -> None:
        nonlocal current, current_len, prev_tail
        if not current:
            return
        text = "\n".join(current)
        chunks.append({"text": text, "section": section})
        prev_tail = text[-overlap_chars:] if overlap_chars > 0 else ""
        current, current_len = [], 0

    def start_new() -> None:
        nonlocal current, current_len
        if prev_tail:
            current, current_len = [prev_tail], len(prev_tail)
        else:
            current, current_len = [], 0

    for element in elements:
        el_type = element.get("type", "")
        text = element.get("text", "")
        if not text:
            continue

        if el_type == "Title":
            # A new section closes the current chunk and resets overlap context.
            flush()
            prev_tail = ""
            section = text
            current, current_len = [text], len(text)
            continue

        if len(text) > chunk_size:
            flush()
            prev_tail = ""
            for piece in _split_long_text(text, chunk_size, overlap_chars):
                chunks.append({"text": piece, "section": section})
            start_new()
            continue

        if current and current_len + 1 + len(text) > chunk_size:
            flush()
            start_new()
        current.append(text)
        current_len += len(text) + (1 if current_len else 0)

    flush()
    return chunks


def load_parsed(path: pathlib.Path) -> dict[str, Any]:
    """Load a parsed document JSON."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_parse_errors(parsed_path: pathlib.Path) -> list[str]:
    """Carry over parse_errors from the parser manifest, if it exists."""
    manifest_path = parsed_path.with_suffix(".manifest.json")
    if not manifest_path.exists():
        return []
    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)
    return list(manifest.get("parse_errors", []))


def run(
    parsed_path: pathlib.Path = FIXTURE_PATH,
    config_path: pathlib.Path = CONFIG_PATH,
    out_dir: pathlib.Path = CHUNKS_DIR,
) -> dict[str, Any]:
    """Chunk one parsed document and write chunks JSON + manifest."""
    config = load_config(config_path)
    chunking = config.get("chunking", {})
    chunk_size = int(chunking.get("chunk_size", 512))
    overlap = float(chunking.get("overlap", 0.0))

    parsed = load_parsed(parsed_path)
    doc_id = parsed["doc_id"]
    source_file = parsed["source_file"]
    department = parsed["department"]

    grouped = chunk_elements(parsed.get("elements", []), chunk_size, overlap)
    chunks = []
    for index, group in enumerate(grouped):
        metadata: dict[str, Any] = {
            "source_file": source_file,
            "department": department,
        }
        if group["section"]:
            metadata["section"] = group["section"]
        chunks.append(
            {"chunk_index": index, "text": group["text"], "metadata": metadata}
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    chunks_doc = {
        "doc_id": doc_id,
        "source_file": source_file,
        "department": department,
        "chunks": chunks,
    }
    chunks_path = out_dir / f"{doc_id}.json"
    with chunks_path.open("w", encoding="utf-8") as f:
        json.dump(chunks_doc, f, ensure_ascii=False, indent=2)

    manifest = {
        "doc_id": doc_id,
        "source_file": source_file,
        "department": department,
        "status": "chunked",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "parse_errors": load_parse_errors(parsed_path),
        "stage": "chunker",
        "chunk_count": len(chunks),
    }
    manifest_path = out_dir / f"{doc_id}.manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return chunks_doc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parsed",
        type=pathlib.Path,
        default=FIXTURE_PATH,
        help="Path to a parsed document JSON (default: dev fixture).",
    )
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=CONFIG_PATH,
        help="Path to pipeline.yaml.",
    )
    parser.add_argument(
        "--out-dir",
        type=pathlib.Path,
        default=CHUNKS_DIR,
        help="Output directory for chunks JSON + manifest.",
    )
    args = parser.parse_args()
    chunks_doc = run(args.parsed, args.config, args.out_dir)
    print(json.dumps(chunks_doc, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

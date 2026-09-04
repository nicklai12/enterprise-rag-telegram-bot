"""embedder.py: sentence-transformers embeddings for chunked documents.

Reads a chunks document (``data/chunks/{doc_id}.json`` in production,
``data/chunks/sample.json`` for development), embeds each chunk's text
with the model configured in ``config/pipeline.yaml:embedding.model``
(default ``BAAI/bge-small-zh-v1.5``), and writes
``data/embeddings/{doc_id}.npy`` (one row per chunk, in the order recorded
in the manifest's ``chunk_order``) plus a ``{doc_id}.manifest.json``
hand-off manifest.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
from typing import Any

import numpy as np
import yaml
from sentence_transformers import SentenceTransformer

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "pipeline.yaml"
CHUNKS_FIXTURE_PATH = ROOT / "data" / "chunks" / "sample.json"
EMBEDDINGS_DIR = ROOT / "data" / "embeddings"

_MODEL_CACHE: dict[str, SentenceTransformer] = {}


def load_config(path: pathlib.Path = CONFIG_PATH) -> dict[str, Any]:
    """Load pipeline configuration from YAML."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_model(model_name: str) -> SentenceTransformer:
    """Load (and cache) a SentenceTransformer model by name."""
    if model_name not in _MODEL_CACHE:
        _MODEL_CACHE[model_name] = SentenceTransformer(model_name)
    return _MODEL_CACHE[model_name]


def load_chunks(path: pathlib.Path) -> dict[str, Any]:
    """Load a chunks JSON document."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_parse_errors(chunks_path: pathlib.Path) -> list[str]:
    """Carry over parse_errors from the chunker manifest, if it exists."""
    manifest_path = chunks_path.with_suffix(".manifest.json")
    if not manifest_path.exists():
        return []
    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)
    return list(manifest.get("parse_errors", []))


def run(
    chunks_path: pathlib.Path = CHUNKS_FIXTURE_PATH,
    config_path: pathlib.Path = CONFIG_PATH,
    out_dir: pathlib.Path = EMBEDDINGS_DIR,
) -> dict[str, Any]:
    """Embed one chunks document and write .npy + manifest."""
    config = load_config(config_path)
    embedding_cfg = config.get("embedding", {})
    model_name = embedding_cfg.get("model", "BAAI/bge-small-zh-v1.5")
    batch_size = int(embedding_cfg.get("batch_size", 16))

    chunks_doc = load_chunks(chunks_path)
    doc_id = chunks_doc["doc_id"]
    source_file = chunks_doc["source_file"]
    department = chunks_doc["department"]
    chunks = chunks_doc["chunks"]

    texts = [chunk["text"] for chunk in chunks]
    chunk_order = [chunk["chunk_index"] for chunk in chunks]

    model = get_model(model_name)
    vectors = np.asarray(
        model.encode(texts, batch_size=batch_size), dtype=np.float32
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    npy_path = out_dir / f"{doc_id}.npy"
    np.save(npy_path, vectors)

    manifest = {
        "doc_id": doc_id,
        "source_file": source_file,
        "department": department,
        "status": "embedded",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "parse_errors": load_parse_errors(chunks_path),
        "stage": "embedder",
        "embedding_model": model_name,
        "embedding_dim": int(vectors.shape[1]),
        "vector_count": int(vectors.shape[0]),
        "chunk_order": chunk_order,
    }
    manifest_path = out_dir / f"{doc_id}.manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chunks",
        type=pathlib.Path,
        default=CHUNKS_FIXTURE_PATH,
        help="Path to a chunks JSON (default: dev fixture).",
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
        default=EMBEDDINGS_DIR,
        help="Output directory for .npy + manifest.",
    )
    args = parser.parse_args()
    manifest = run(args.chunks, args.config, args.out_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

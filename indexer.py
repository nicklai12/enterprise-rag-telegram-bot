"""indexer.py: write embedded chunks to Chroma as a candidate kb_run_id.

Reads a chunks document (``data/chunks/{doc_id}.json`` in production,
``data/chunks/sample.json`` for development) and its embeddings
(``data/embeddings/{doc_id}.npy``, produced by embedder.py), then upserts
every chunk into the Chroma data collection named by
``config/pipeline.yaml:vectorstore.data_collection_name`` (default
``company_kb_data``) under a fresh ``kb_run_id`` (format ``YYYYMMDD-HHMMSS``).

Chunk ids follow spec §4.3: ``{kb_run_id}_{doc_id}_{chunk_index}`` so different
kb_run_id versions coexist without overwriting each other. Metadata carries
``kb_run_id`` / ``doc_id`` / ``chunk_index`` / ``source_file`` / ``department``.

Connection: ``chromadb.CloudClient`` with credentials from the environment
variables ``CHROMA_API_KEY`` / ``CHROMA_TENANT`` / ``CHROMA_DATABASE``.
Any connection or write failure exits with a non-zero exit code (whole
batch aborted), per spec §5.6.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
from typing import Any

import chromadb
import numpy as np
import yaml

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "pipeline.yaml"
CHUNKS_FIXTURE_PATH = ROOT / "data" / "chunks" / "sample.json"
EMBEDDINGS_FIXTURE_PATH = ROOT / "data" / "embeddings" / "sample.npy"


def load_config(path: pathlib.Path = CONFIG_PATH) -> dict[str, Any]:
    """Load pipeline configuration from YAML."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def new_kb_run_id(now: datetime.datetime | None = None) -> str:
    """Generate a kb_run_id in the spec-recommended YYYYMMDD-HHMMSS format."""
    if now is None:
        now = datetime.datetime.now()
    return now.strftime("%Y%m%d-%H%M%S")


def connect_chroma() -> chromadb.CloudClient:
    """Connect to Chroma Cloud using credentials from environment variables."""
    return chromadb.CloudClient(
        api_key=os.environ["CHROMA_API_KEY"],
        tenant=os.environ["CHROMA_TENANT"],
        database=os.environ["CHROMA_DATABASE"],
    )


def load_chunks(path: pathlib.Path) -> dict[str, Any]:
    """Load a chunks JSON document."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def run(
    chunks_path: pathlib.Path = CHUNKS_FIXTURE_PATH,
    embeddings_path: pathlib.Path = EMBEDDINGS_FIXTURE_PATH,
    config_path: pathlib.Path = CONFIG_PATH,
    client: chromadb.ClientAPI | None = None,
    kb_run_id: str | None = None,
) -> dict[str, Any]:
    """Upsert one document's chunks+embeddings into the data collection."""
    config = load_config(config_path)
    collection_name = config.get("vectorstore", {}).get(
        "data_collection_name", "company_kb_data"
    )
    if kb_run_id is None:
        kb_run_id = new_kb_run_id()
    if client is None:
        client = connect_chroma()

    chunks_doc = load_chunks(chunks_path)
    doc_id = chunks_doc["doc_id"]
    source_file = chunks_doc["source_file"]
    department = chunks_doc["department"]
    chunks = chunks_doc["chunks"]

    vectors = np.load(embeddings_path)
    if vectors.shape[0] != len(chunks):
        raise ValueError(
            f"embedding rows ({vectors.shape[0]}) != chunk count ({len(chunks)}) "
            f"for doc_id={doc_id}"
        )

    ids = [f"{kb_run_id}_{doc_id}_{chunk['chunk_index']}" for chunk in chunks]
    metadatas = [
        {
            "kb_run_id": kb_run_id,
            "doc_id": doc_id,
            "chunk_index": int(chunk["chunk_index"]),
            "source_file": source_file,
            "department": department,
        }
        for chunk in chunks
    ]
    documents = [chunk["text"] for chunk in chunks]

    collection = client.get_or_create_collection(collection_name)
    collection.upsert(
        ids=ids,
        metadatas=metadatas,
        documents=documents,
        embeddings=vectors.tolist(),
    )

    return {
        "doc_id": doc_id,
        "source_file": source_file,
        "department": department,
        "status": "indexed",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "parse_errors": [],
        "stage": "indexer",
        "kb_run_id": kb_run_id,
        "collection_name": collection_name,
        "upsert_count": len(ids),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chunks",
        type=pathlib.Path,
        default=CHUNKS_FIXTURE_PATH,
        help="Path to a chunks JSON (default: dev fixture).",
    )
    parser.add_argument(
        "--embeddings",
        type=pathlib.Path,
        default=EMBEDDINGS_FIXTURE_PATH,
        help="Path to the matching .npy embeddings (default: dev fixture).",
    )
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=CONFIG_PATH,
        help="Path to pipeline.yaml.",
    )
    args = parser.parse_args()
    try:
        manifest = run(args.chunks, args.embeddings, args.config)
    except Exception as exc:
        print(f"indexer failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

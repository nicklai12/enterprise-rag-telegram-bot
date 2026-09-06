"""publisher.py: publish a verified candidate kb_run_id via the active pointer.

Per spec §5.8, this script runs only after verify_index.py has passed (the
workflow guarantees the ordering), so it does NOT re-run the audit. It writes
one record to the Chroma control collection named by
``config/pipeline.yaml:vectorstore.control_collection_name`` (default
``company_kb_control``) under the fixed id
``vectorstore.control_pointer_id`` (default ``active_pointer``):

    metadata: {"active_kb_run_id": "<candidate_kb_run_id>",
               "published_at": "<ISO-8601 UTC>"}

The single-record upsert is atomic: on any connection/write failure the script
exits with a non-zero code and the previously published pointer is left
untouched (no partial/dirty state).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "pipeline.yaml"


def load_config(path: pathlib.Path = CONFIG_PATH) -> dict[str, Any]:
    """Load pipeline configuration from YAML."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def connect_chroma() -> Any:
    """Connect to Chroma Cloud using credentials from environment variables."""
    import chromadb

    return chromadb.CloudClient(
        api_key=os.environ["CHROMA_API_KEY"],
        tenant=os.environ["CHROMA_TENANT"],
        database=os.environ["CHROMA_DATABASE"],
    )


def run(
    kb_run_id: str,
    config_path: pathlib.Path = CONFIG_PATH,
    client: Any = None,
) -> dict[str, Any]:
    """Upsert the active pointer in the control collection."""
    config = load_config(config_path)
    vectorstore = config.get("vectorstore", {})
    collection_name = vectorstore.get("control_collection_name", "company_kb_control")
    pointer_id = vectorstore.get("control_pointer_id", "active_pointer")
    if client is None:
        client = connect_chroma()

    published_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    collection = client.get_or_create_collection(collection_name)
    collection.upsert(
        ids=[pointer_id],
        metadatas=[
            {
                "active_kb_run_id": kb_run_id,
                "published_at": published_at,
            }
        ],
        documents=[f"active pointer -> kb_run_id {kb_run_id}"],
    )

    return {
        "status": "published",
        "timestamp": published_at,
        "kb_run_id": kb_run_id,
        "collection_name": collection_name,
        "pointer_id": pointer_id,
        "active_kb_run_id": kb_run_id,
        "published_at": published_at,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kb-run-id",
        required=True,
        help="Candidate kb_run_id to publish (must have passed verify).",
    )
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=CONFIG_PATH,
        help="Path to pipeline.yaml.",
    )
    args = parser.parse_args()
    try:
        manifest = run(args.kb_run_id, args.config)
    except Exception as exc:
        print(f"publisher failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

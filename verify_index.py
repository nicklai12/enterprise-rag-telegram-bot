"""verify_index.py: read-only audit of a candidate kb_run_id.

Per spec §5.7, a candidate version is publishable only if all three checks
pass:

1. count match — expected chunk total (summed from the chunks manifests)
   vs. actual rows in the Chroma data collection where ``kb_run_id ==
   candidate``.
2. no duplicate ids within the kb_run_id scope.
3. golden QA hit rate >= 80% — retrieve top_n (``retrieval.golden_top_k``
   from pipeline.yaml) per question in tests/golden_qa.yaml and check the
   returned metadata for ``expected_doc_id``.

Any failure → non-zero exit code and a ``failed`` entry (with the concrete
numbers) appended to status/kb_status.json. This script never updates the
active pointer; that is publisher.py's job.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
from typing import Any, Callable

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "pipeline.yaml"
CHUNKS_DIR = ROOT / "data" / "chunks"
GOLDEN_QA_PATH = ROOT / "tests" / "golden_qa.yaml"
STATUS_PATH = ROOT / "status" / "kb_status.json"

GOLDEN_HIT_RATE_THRESHOLD = 0.8

EmbedFn = Callable[[list[str]], list[list[float]]]


def load_config(path: pathlib.Path = CONFIG_PATH) -> dict[str, Any]:
    """Load pipeline configuration from YAML."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_golden_qa(path: pathlib.Path = GOLDEN_QA_PATH) -> list[dict[str, str]]:
    """Load the golden QA list (each item: question + expected_doc_id)."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def expected_chunk_count(chunks_dir: pathlib.Path = CHUNKS_DIR) -> int:
    """Sum chunk_count across all chunk manifests in the directory."""
    total = 0
    for manifest_path in sorted(chunks_dir.glob("*.manifest.json")):
        with manifest_path.open("r", encoding="utf-8") as f:
            total += int(json.load(f)["chunk_count"])
    return total


def audit_kb_run(
    collection: Any,
    kb_run_id: str,
    expected_chunks: int,
    golden_qa: list[dict[str, str]],
    embed_fn: EmbedFn,
    golden_top_k: int,
) -> dict[str, Any]:
    """Run the three read-only checks against a candidate kb_run_id."""
    found = collection.get(
        where={"kb_run_id": kb_run_id},
        include=["metadatas"],
    )
    ids: list[str] = list(found["ids"])
    actual_chunks = len(ids)
    unique_ids = set(ids)
    duplicate_ids = sorted({i for i in ids if ids.count(i) > 1})

    questions = [item["question"] for item in golden_qa]
    query_vectors = embed_fn(questions) if questions else []
    details = []
    hits = 0
    for item, vector in zip(golden_qa, query_vectors):
        result = collection.query(
            query_embeddings=[vector],
            n_results=golden_top_k,
            where={"kb_run_id": kb_run_id},
            include=["metadatas"],
        )
        hit = any(
            meta.get("doc_id") == item["expected_doc_id"]
            for meta in result["metadatas"][0]
        )
        hits += int(hit)
        details.append(
            {
                "question": item["question"],
                "expected_doc_id": item["expected_doc_id"],
                "hit": hit,
            }
        )
    total_q = len(golden_qa)
    hit_rate = (hits / total_q) if total_q else 1.0

    count_check = {
        "passed": actual_chunks == expected_chunks,
        "expected": expected_chunks,
        "actual": actual_chunks,
    }
    duplicate_check = {
        "passed": len(duplicate_ids) == 0,
        "total": actual_chunks,
        "unique": len(unique_ids),
        "duplicate_ids": duplicate_ids,
    }
    golden_check = {
        "passed": hit_rate >= GOLDEN_HIT_RATE_THRESHOLD,
        "total": total_q,
        "hits": hits,
        "hit_rate": round(hit_rate, 4),
        "threshold": GOLDEN_HIT_RATE_THRESHOLD,
        "top_k": golden_top_k,
        "details": details,
    }
    checks = {
        "count_match": count_check,
        "no_duplicate_ids": duplicate_check,
        "golden_qa_hit_rate": golden_check,
    }
    return {
        "kb_run_id": kb_run_id,
        "passed": all(c["passed"] for c in checks.values()),
        "checks": checks,
    }


def _default_embed_fn(config: dict[str, Any]) -> EmbedFn:
    """Embed questions with the pipeline's sentence-transformers model."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(config["embedding"]["model"])
    return lambda texts: model.encode(texts).tolist()


def connect_data_collection(config: dict[str, Any]) -> Any:
    """Connect to the Chroma Cloud data collection (credentials via env)."""
    import chromadb

    client = chromadb.CloudClient(
        api_key=os.environ["CHROMA_API_KEY"],
        tenant=os.environ["CHROMA_TENANT"],
        database=os.environ["CHROMA_DATABASE"],
    )
    return client.get_collection(
        config["vectorstore"]["data_collection_name"]
    )


def _update_status(
    status_path: pathlib.Path,
    kb_run_id: str,
    report: dict[str, Any],
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    """Append a run entry to kb_status.json (never touches the pointer)."""
    if status_path.exists():
        with status_path.open("r", encoding="utf-8") as f:
            status = json.load(f)
    else:
        status = {"updated_at": None, "active_kb_run_id": None, "runs": []}
    status["runs"].append(
        {
            "kb_run_id": kb_run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": "success" if report["passed"] else "failed",
            "summary": report,
        }
    )
    status["updated_at"] = finished_at
    status_path.parent.mkdir(parents=True, exist_ok=True)
    with status_path.open("w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    return status


def run(
    kb_run_id: str,
    config_path: pathlib.Path = CONFIG_PATH,
    chunks_dir: pathlib.Path = CHUNKS_DIR,
    golden_qa_path: pathlib.Path = GOLDEN_QA_PATH,
    status_path: pathlib.Path = STATUS_PATH,
    collection: Any = None,
    embed_fn: EmbedFn = None,
) -> dict[str, Any]:
    """Audit a candidate kb_run_id and record the outcome in kb_status.json."""
    config = load_config(config_path)
    golden_top_k = int(config["retrieval"]["golden_top_k"])
    if collection is None:
        collection = connect_data_collection(config)
    if embed_fn is None:
        embed_fn = _default_embed_fn(config)

    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report = audit_kb_run(
        collection=collection,
        kb_run_id=kb_run_id,
        expected_chunks=expected_chunk_count(chunks_dir),
        golden_qa=load_golden_qa(golden_qa_path),
        embed_fn=embed_fn,
        golden_top_k=golden_top_k,
    )
    finished_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _update_status(status_path, kb_run_id, report, started_at, finished_at)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kb-run-id",
        required=True,
        help="Candidate kb_run_id to audit.",
    )
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=CONFIG_PATH,
        help="Path to pipeline.yaml.",
    )
    parser.add_argument(
        "--chunks-dir",
        type=pathlib.Path,
        default=CHUNKS_DIR,
        help="Directory of chunks JSON + manifests (expected count source).",
    )
    parser.add_argument(
        "--golden-qa",
        type=pathlib.Path,
        default=GOLDEN_QA_PATH,
        help="Path to the golden QA YAML.",
    )
    parser.add_argument(
        "--status",
        type=pathlib.Path,
        default=STATUS_PATH,
        help="Path to kb_status.json.",
    )
    args = parser.parse_args()

    report = run(
        kb_run_id=args.kb_run_id,
        config_path=args.config,
        chunks_dir=args.chunks_dir,
        golden_qa_path=args.golden_qa,
        status_path=args.status,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

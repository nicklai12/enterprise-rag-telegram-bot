"""Tests for verify_index.py.

All tests use a local ephemeral ChromaDB instance and a deterministic
keyword-bag embed function — no Chroma Cloud / model download calls.
"""
import json
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chromadb
import pytest
import yaml

import verify_index

CONFIG_PATH = ROOT / "config" / "pipeline.yaml"
GOLDEN_TOP_K = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))[
    "retrieval"
]["golden_top_k"]

KB_RUN_ID = "20260906-120000"
VOCAB = ["特休", "事假", "病假", "差旅"]


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic keyword-bag vectors over VOCAB."""
    return [[float(t.count(k)) for k in VOCAB] for t in texts]


def _build_collection(docs: dict[str, list[str]]):
    """Create an ephemeral collection; each text becomes one chunk."""
    client = chromadb.EphemeralClient()
    # Unique name per call: the ephemeral system DB is shared in-process,
    # so a fixed name would leak chunks between tests.
    collection = client.create_collection(f"kb_data_{uuid.uuid4().hex}")
    ids, documents, metadatas = [], [], []
    for doc_id, texts in docs.items():
        for chunk_index, text in enumerate(texts):
            ids.append(f"{KB_RUN_ID}_{doc_id}_{chunk_index}")
            documents.append(text)
            metadatas.append(
                {
                    "kb_run_id": KB_RUN_ID,
                    "doc_id": doc_id,
                    "chunk_index": chunk_index,
                    "source_file": f"data/raw/HR/{doc_id}.txt",
                    "department": "HR",
                }
            )
    collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=fake_embed(documents),
    )
    return collection


def _write_chunks_manifest(tmp_path: pathlib.Path, doc_id: str, chunk_count: int):
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    manifest = {
        "doc_id": doc_id,
        "status": "chunked",
        "chunk_count": chunk_count,
    }
    (chunks_dir / f"{doc_id}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return chunks_dir


def _write_golden_qa(tmp_path: pathlib.Path, items: list[dict]) -> pathlib.Path:
    path = tmp_path / "golden_qa.yaml"
    path.write_text(
        yaml.safe_dump(items, allow_unicode=True), encoding="utf-8"
    )
    return path


def _run(tmp_path, collection, chunks_dir, golden_qa_path):
    return verify_index.run(
        kb_run_id=KB_RUN_ID,
        config_path=CONFIG_PATH,
        chunks_dir=chunks_dir,
        golden_qa_path=golden_qa_path,
        status_path=tmp_path / "kb_status.json",
        collection=collection,
        embed_fn=fake_embed,
    )


DOCS = {
    "leave_rules": ["特休：每滿六個月可享三日特休。"],
    "personal_leave": ["事假：每次至少半日，全年不得超過十四日。"],
    "sick_leave": ["病假：檢附醫療證明，全年不得超過三十日。"],
}


def test_count_mismatch_fails(tmp_path):
    """Manifest expects 3 chunks; Chroma holds 2 → audit must fail."""
    collection = _build_collection(
        {
            "leave_rules": DOCS["leave_rules"],
            "personal_leave": DOCS["personal_leave"],
        }
    )
    chunks_dir = _write_chunks_manifest(tmp_path, "sample", 3)
    golden = _write_golden_qa(
        tmp_path,
        [
            {
                "question": "特休有幾天？",
                "expected_doc_id": "leave_rules",
            }
        ],
    )

    report = _run(tmp_path, collection, chunks_dir, golden)

    assert report["passed"] is False
    check = report["checks"]["count_match"]
    assert check["passed"] is False
    assert check["expected"] == 3
    assert check["actual"] == 2
    status = json.loads((tmp_path / "kb_status.json").read_text("utf-8"))
    assert status["runs"][-1]["status"] == "failed"
    assert status["active_kb_run_id"] is None


def test_duplicate_ids_fail(tmp_path):
    """Duplicate ids inside the kb_run_id scope → audit must fail."""

    class DupIdCollection:
        """Wraps a real collection but reports a duplicated id from get()."""

        def __init__(self, inner):
            self._inner = inner

        def get(self, **kwargs):
            result = self._inner.get(**kwargs)
            result["ids"] = result["ids"] + [result["ids"][0]]
            return result

        def query(self, **kwargs):
            return self._inner.query(**kwargs)

    collection = DupIdCollection(_build_collection(DOCS))
    chunks_dir = _write_chunks_manifest(tmp_path, "sample", 3)
    golden = _write_golden_qa(
        tmp_path,
        [{"question": "特休有幾天？", "expected_doc_id": "leave_rules"}],
    )

    report = _run(tmp_path, collection, chunks_dir, golden)

    assert report["passed"] is False
    check = report["checks"]["no_duplicate_ids"]
    assert check["passed"] is False
    assert check["total"] == 4
    assert check["unique"] == 3
    assert check["duplicate_ids"] == [f"{KB_RUN_ID}_leave_rules_0"]


def test_golden_hit_rate_66_percent_fails(tmp_path):
    """2 of 3 golden questions hit (66%) < 80% → audit must fail."""
    collection = _build_collection(DOCS)
    chunks_dir = _write_chunks_manifest(tmp_path, "sample", 3)
    golden = _write_golden_qa(
        tmp_path,
        [
            {"question": "特休有幾天？", "expected_doc_id": "leave_rules"},
            {"question": "事假如何申請？", "expected_doc_id": "personal_leave"},
            {"question": "差旅費如何報銷？", "expected_doc_id": "missing_doc"},
        ],
    )

    report = _run(tmp_path, collection, chunks_dir, golden)

    assert report["passed"] is False
    check = report["checks"]["golden_qa_hit_rate"]
    assert check["passed"] is False
    assert check["hits"] == 2
    assert check["total"] == 3
    assert check["hit_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert check["threshold"] == 0.8
    assert check["top_k"] == GOLDEN_TOP_K


def test_all_checks_pass(tmp_path):
    """3 of 3 golden questions hit, counts match, no duplicates → pass."""
    collection = _build_collection(DOCS)
    chunks_dir = _write_chunks_manifest(tmp_path, "sample", 3)
    golden = _write_golden_qa(
        tmp_path,
        [
            {"question": "特休有幾天？", "expected_doc_id": "leave_rules"},
            {"question": "事假如何申請？", "expected_doc_id": "personal_leave"},
            {"question": "病假需要什麼證明？", "expected_doc_id": "sick_leave"},
        ],
    )

    report = _run(tmp_path, collection, chunks_dir, golden)

    assert report["passed"] is True
    assert report["checks"]["count_match"]["passed"] is True
    assert report["checks"]["count_match"]["actual"] == 3
    assert report["checks"]["no_duplicate_ids"]["passed"] is True
    golden_check = report["checks"]["golden_qa_hit_rate"]
    assert golden_check["passed"] is True
    assert golden_check["hits"] == 3
    assert golden_check["hit_rate"] == 1.0
    status = json.loads((tmp_path / "kb_status.json").read_text("utf-8"))
    assert status["runs"][-1]["status"] == "success"
    assert status["active_kb_run_id"] is None

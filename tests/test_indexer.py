"""Tests for indexer.py (all against a local ephemeral Chroma client)."""
import json
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chromadb
import jsonschema

import indexer

SCHEMA_DIR = ROOT / "schemas"
CONFIG_PATH = ROOT / "config" / "pipeline.yaml"
COLLECTION_NAME = "company_kb_data"


def _write_fixture(tmp_path: pathlib.Path, texts: list[str]) -> tuple[pathlib.Path, pathlib.Path]:
    """Write a chunks JSON + matching .npy with synthetic fixed-dim vectors."""
    doc = {
        "doc_id": "test_doc",
        "source_file": "data/raw/HR/test_doc.txt",
        "department": "HR",
        "chunks": [
            {
                "chunk_index": i,
                "text": text,
                "metadata": {
                    "source_file": "data/raw/HR/test_doc.txt",
                    "department": "HR",
                },
            }
            for i, text in enumerate(texts)
        ],
    }
    chunks_path = tmp_path / "test_doc.json"
    chunks_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    vectors = np.arange(len(texts) * 8, dtype=np.float32).reshape(len(texts), 8)
    npy_path = tmp_path / "test_doc.npy"
    np.save(npy_path, vectors)
    return chunks_path, npy_path


TEXTS_3 = [
    "一、特休。員工每滿六個月可享三日特休。",
    "二、事假。因私事需處理者，每次至少半日。",
    "三、病假。檢附醫療證明，全年不得超過三十日。",
]


def test_ephemeral_write_then_query_by_kb_run_id(tmp_path: pathlib.Path):
    """DoD 1: written chunks are retrievable via where kb_run_id, count matches input."""
    chunks_path, npy_path = _write_fixture(tmp_path, TEXTS_3)
    client = chromadb.EphemeralClient()

    manifest = indexer.run(
        chunks_path, npy_path, CONFIG_PATH, client=client, kb_run_id="20260904-120000"
    )

    assert manifest["stage"] == "indexer"
    assert manifest["kb_run_id"] == "20260904-120000"
    assert manifest["upsert_count"] == 3

    collection = client.get_collection(COLLECTION_NAME)
    results = collection.get(
        where={"kb_run_id": "20260904-120000"}, include=["metadatas", "documents", "embeddings"]
    )
    assert len(results["ids"]) == 3
    assert sorted(results["ids"]) == [
        "20260904-120000_test_doc_0",
        "20260904-120000_test_doc_1",
        "20260904-120000_test_doc_2",
    ]
    for meta in results["metadatas"]:
        assert meta["kb_run_id"] == "20260904-120000"
        assert meta["doc_id"] == "test_doc"
        assert meta["source_file"] == "data/raw/HR/test_doc.txt"
        assert meta["department"] == "HR"
    assert sorted(meta["chunk_index"] for meta in results["metadatas"]) == [0, 1, 2]
    assert sorted(results["documents"]) == sorted(TEXTS_3)
    assert np.asarray(results["embeddings"]).shape == (3, 8)

    with (SCHEMA_DIR / "manifest.schema.json").open("r", encoding="utf-8") as f:
        jsonschema.validate(instance=manifest, schema=json.load(f))


def test_two_kb_run_ids_for_same_doc_coexist(tmp_path: pathlib.Path):
    """DoD 3: same doc_id under two kb_run_ids → both versions coexist."""
    chunks_path, npy_path = _write_fixture(tmp_path, TEXTS_3)
    client = chromadb.EphemeralClient()

    indexer.run(chunks_path, npy_path, CONFIG_PATH, client=client, kb_run_id="20260904-120000")
    indexer.run(chunks_path, npy_path, CONFIG_PATH, client=client, kb_run_id="20260904-130000")

    collection = client.get_collection(COLLECTION_NAME)
    assert collection.count() == 6
    for run_id, expected_ids in [
        ("20260904-120000", [f"20260904-120000_test_doc_{i}" for i in range(3)]),
        ("20260904-130000", [f"20260904-130000_test_doc_{i}" for i in range(3)]),
    ]:
        results = collection.get(where={"kb_run_id": run_id})
        assert sorted(results["ids"]) == sorted(expected_ids)


def test_connection_failure_exits_nonzero(tmp_path: pathlib.Path, monkeypatch, capsys):
    """DoD 2a: connect_chroma raising → main exits with code 1."""
    chunks_path, npy_path = _write_fixture(tmp_path, TEXTS_3)

    def _boom():
        raise RuntimeError("cannot reach Chroma Cloud")

    monkeypatch.setattr(indexer, "connect_chroma", _boom)
    monkeypatch.setattr(
        sys,
        "argv",
        ["indexer.py", "--chunks", str(chunks_path), "--embeddings", str(npy_path)],
    )
    with pytest.raises(SystemExit) as exc_info:
        indexer.main()
    assert exc_info.value.code == 1
    assert "indexer failed" in capsys.readouterr().err


def test_missing_env_credentials_exits_nonzero(tmp_path, monkeypatch, capsys):
    """DoD 2b: missing CHROMA_* env vars → main exits with code 1."""
    chunks_path, npy_path = _write_fixture(tmp_path, TEXTS_3)
    for var in ("CHROMA_API_KEY", "CHROMA_TENANT", "CHROMA_DATABASE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["indexer.py", "--chunks", str(chunks_path), "--embeddings", str(npy_path)],
    )
    with pytest.raises(SystemExit) as exc_info:
        indexer.main()
    assert exc_info.value.code != 0
    assert "indexer failed" in capsys.readouterr().err


def test_write_failure_exits_nonzero(tmp_path, monkeypatch, capsys):
    """DoD 2c: collection write failure → main exits with code 1."""
    chunks_path, npy_path = _write_fixture(tmp_path, TEXTS_3)

    class _FailingClient:
        def get_or_create_collection(self, name):
            raise RuntimeError("write quota exceeded")

    monkeypatch.setattr(indexer, "connect_chroma", lambda: _FailingClient())
    monkeypatch.setattr(
        sys,
        "argv",
        ["indexer.py", "--chunks", str(chunks_path), "--embeddings", str(npy_path)],
    )
    with pytest.raises(SystemExit) as exc_info:
        indexer.main()
    assert exc_info.value.code != 0
    assert "indexer failed" in capsys.readouterr().err


def test_new_kb_run_id_format():
    """kb_run_id matches the spec-recommended YYYYMMDD-HHMMSS format."""
    import datetime as dt

    run_id = indexer.new_kb_run_id(dt.datetime(2026, 9, 4, 13, 30, 45))
    assert run_id == "20260904-133045"
    assert len(run_id) == 15 and run_id[8] == "-"

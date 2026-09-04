"""Tests for embedder.py."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import jsonschema
import numpy as np
import pytest
import yaml

import embedder

SCHEMA_DIR = ROOT / "schemas"
FIXTURE_CHUNKS = ROOT / "data" / "chunks" / "sample.json"
CONFIG_PATH = ROOT / "config" / "pipeline.yaml"


def _load_json(path: pathlib.Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_chunks(tmp_path: pathlib.Path, texts: list[str]) -> pathlib.Path:
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
    path = tmp_path / "test_doc.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


TEXTS_3 = [
    "一、特休。員工每滿六個月可享三日特休。",
    "二、事假。因私事需處理者，每次至少半日。",
    "三、病假。檢附醫療證明，全年不得超過三十日。",
]


def test_fixture_run_npy_rows_match_chunk_count(tmp_path: pathlib.Path):
    """Dev fixture: .npy row count == chunk count, manifest validates."""
    manifest = embedder.run(
        chunks_path=FIXTURE_CHUNKS,
        config_path=CONFIG_PATH,
        out_dir=tmp_path,
    )

    assert manifest["stage"] == "embedder"
    assert manifest["status"] == "embedded"

    vectors = np.load(tmp_path / "sample.npy")
    assert vectors.shape[0] == manifest["vector_count"]
    assert len(manifest["chunk_order"]) == vectors.shape[0]

    jsonschema.validate(
        instance=manifest, schema=_load_json(SCHEMA_DIR / "embeddings_manifest.schema.json")
    )


def test_npy_row_count_and_dim_match_chunks(tmp_path: pathlib.Path):
    """3-chunk fixture: npy.shape == (3, model_dim); dim comes from model output."""
    chunks_path = _write_chunks(tmp_path, TEXTS_3)

    manifest = embedder.run(chunks_path, CONFIG_PATH, tmp_path / "out")

    vectors = np.load(tmp_path / "out" / "test_doc.npy")
    assert vectors.shape[0] == 3
    assert manifest["vector_count"] == 3
    assert manifest["chunk_order"] == [0, 1, 2]

    # embedding_dim must equal the model's actual output dim, not a hardcoded number.
    model = embedder.get_model(manifest["embedding_model"])
    actual_dim = np.asarray(model.encode([TEXTS_3[0]])).shape[1]
    assert manifest["embedding_dim"] == actual_dim == vectors.shape[1]


def test_chunk_order_maps_rows_to_chunk_index(tmp_path: pathlib.Path):
    """Shuffled chunk_index values are recorded explicitly in chunk_order."""
    chunks_path = _write_chunks(tmp_path, TEXTS_3)
    doc = _load_json(chunks_path)
    shuffled = [doc["chunks"][2], doc["chunks"][0], doc["chunks"][1]]
    for new_pos, chunk in enumerate(shuffled):
        chunk["chunk_index"] = [7, 3, 9][new_pos]
    doc["chunks"] = shuffled
    chunks_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    manifest = embedder.run(chunks_path, CONFIG_PATH, tmp_path / "out")

    assert manifest["chunk_order"] == [7, 3, 9]
    vectors = np.load(tmp_path / "out" / "test_doc.npy")
    assert vectors.shape[0] == 3


def test_parse_errors_carried_over_from_chunker_manifest(tmp_path: pathlib.Path):
    """If a chunker manifest exists beside the chunks JSON, its parse_errors carry over."""
    chunks_path = _write_chunks(tmp_path, TEXTS_3)
    manifest = {
        "doc_id": "test_doc",
        "source_file": "data/raw/HR/test_doc.txt",
        "department": "HR",
        "status": "chunked",
        "timestamp": "2026-09-02T00:00:00Z",
        "parse_errors": ["page 3: table skipped"],
        "stage": "chunker",
        "chunk_count": 3,
    }
    chunks_path.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    out_manifest = embedder.run(chunks_path, CONFIG_PATH, tmp_path / "out")

    assert out_manifest["parse_errors"] == ["page 3: table skipped"]

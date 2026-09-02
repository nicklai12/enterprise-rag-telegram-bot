"""Tests for chunker.py."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import jsonschema
import pytest
import yaml

import chunker

SCHEMA_DIR = ROOT / "schemas"
FIXTURE_PATH = ROOT / "data" / "fixtures" / "sample_parsed.json"


def _load_json(path: pathlib.Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_config(tmp_path: pathlib.Path, chunk_size: int, overlap: float) -> pathlib.Path:
    config = {
        "chunking": {
            "strategy": "hierarchical",
            "chunk_size": chunk_size,
            "overlap": overlap,
        }
    }
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


def _write_parsed(tmp_path: pathlib.Path, elements: list[dict]) -> pathlib.Path:
    doc = {
        "doc_id": "test_doc",
        "source_file": "data/raw/HR/test_doc.txt",
        "department": "HR",
        "elements": elements,
    }
    path = tmp_path / "test_doc.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


LONG_ELEMENTS = [
    {"type": "Title", "text": "人力資源部請假規則", "metadata": {}},
    {"type": "NarrativeText", "text": "一、特休。" + "員工每滿六個月可享三日特休。" * 20, "metadata": {}},
    {"type": "NarrativeText", "text": "二、事假。因私事需處理者，每次至少半日。", "metadata": {}},
    {"type": "NarrativeText", "text": "三、病假。檢附醫療證明，全年不得超過三十日。", "metadata": {}},
]


def test_fixture_run_chunk_count_matches_and_schema_valid(tmp_path: pathlib.Path):
    """Running on the dev fixture: manifest chunk_count == len(chunks), both validate."""
    chunks_doc = chunker.run(
        parsed_path=FIXTURE_PATH,
        config_path=ROOT / "config" / "pipeline.yaml",
        out_dir=tmp_path,
    )

    assert chunks_doc["doc_id"] == "sample"
    written = _load_json(tmp_path / "sample.json")
    assert written == chunks_doc

    manifest = _load_json(tmp_path / "sample.manifest.json")
    assert manifest["stage"] == "chunker"
    assert manifest["status"] == "chunked"
    assert manifest["chunk_count"] == len(chunks_doc["chunks"])

    jsonschema.validate(
        instance=chunks_doc, schema=_load_json(SCHEMA_DIR / "chunks.schema.json")
    )
    jsonschema.validate(
        instance=manifest, schema=_load_json(SCHEMA_DIR / "manifest.schema.json")
    )


def test_chunk_count_changes_with_chunk_size(tmp_path: pathlib.Path):
    """Different chunk_size settings produce different chunk counts (config is read)."""
    parsed_path = _write_parsed(tmp_path, LONG_ELEMENTS)
    out_512 = tmp_path / "out_512"
    out_64 = tmp_path / "out_64"

    doc_512 = chunker.run(parsed_path, _write_config(tmp_path, 512, 0.2), out_512)
    doc_64 = chunker.run(parsed_path, _write_config(tmp_path, 64, 0.2), out_64)

    count_512 = len(doc_512["chunks"])
    count_64 = len(doc_64["chunks"])
    assert count_64 > count_512 > 0
    assert _load_json(out_512 / "test_doc.manifest.json")["chunk_count"] == count_512
    assert _load_json(out_64 / "test_doc.manifest.json")["chunk_count"] == count_64


def test_chunk_texts_respect_chunk_size(tmp_path: pathlib.Path):
    """No greedy-grouped chunk exceeds chunk_size (hard-split pieces may equal it)."""
    parsed_path = _write_parsed(tmp_path, LONG_ELEMENTS)
    doc = chunker.run(parsed_path, _write_config(tmp_path, 64, 0.2), tmp_path / "out")

    assert all(len(c["text"]) <= 64 for c in doc["chunks"])


def test_overlap_is_applied_between_split_pieces(tmp_path: pathlib.Path):
    """A long element is split into overlapping pieces (overlap from config)."""
    elements = [
        {
            "type": "NarrativeText",
            "text": "".join(f"{i:02d}" for i in range(50)),  # 100 chars, no titles
            "metadata": {},
        }
    ]
    parsed_path = _write_parsed(tmp_path, elements)

    doc = chunker.run(parsed_path, _write_config(tmp_path, 30, 0.2), tmp_path / "out")

    pieces = [c["text"] for c in doc["chunks"]]
    assert len(pieces) > 1
    overlap_chars = int(30 * 0.2)
    for prev, nxt in zip(pieces, pieces[1:]):
        k = min(overlap_chars, len(nxt))
        assert nxt[:k] == prev[-k:]


def test_title_becomes_section_context(tmp_path: pathlib.Path):
    """Chunks after a Title carry it as hierarchical section metadata."""
    parsed_path = _write_parsed(tmp_path, LONG_ELEMENTS)
    doc = chunker.run(parsed_path, _write_config(tmp_path, 64, 0.2), tmp_path / "out")

    assert doc["chunks"][0]["metadata"]["section"] == "人力資源部請假規則"
    assert all(
        c["metadata"]["source_file"] == "data/raw/HR/test_doc.txt"
        and c["metadata"]["department"] == "HR"
        for c in doc["chunks"]
    )
    assert [c["chunk_index"] for c in doc["chunks"]] == list(
        range(len(doc["chunks"]))
    )


def test_parse_errors_carried_over_from_parser_manifest(tmp_path: pathlib.Path):
    """If a parser manifest exists beside the parsed JSON, its parse_errors carry over."""
    parsed_path = _write_parsed(tmp_path, LONG_ELEMENTS[:1])
    manifest = {
        "doc_id": "test_doc",
        "source_file": "data/raw/HR/test_doc.txt",
        "department": "HR",
        "status": "parsed",
        "timestamp": "2026-09-02T00:00:00Z",
        "parse_errors": ["page 3: table skipped"],
        "stage": "parser",
    }
    parsed_path.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    chunker.run(parsed_path, _write_config(tmp_path, 512, 0.2), tmp_path / "out")

    out_manifest = _load_json(tmp_path / "out" / "test_doc.manifest.json")
    assert out_manifest["parse_errors"] == ["page 3: table skipped"]

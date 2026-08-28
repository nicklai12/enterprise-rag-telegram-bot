"""Schema and fixture validation tests for the knowledge-base pipeline contracts."""
import json
import pathlib

import jsonschema
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schemas"
FIXTURE_DIR = ROOT / "data" / "fixtures"


def _load_json(path: pathlib.Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_yaml(path: pathlib.Path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_all_schemas_are_valid_json_schemas():
    """Every *.schema.json can be loaded as a JSON Schema (Draft 7)."""
    for schema_path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        schema = _load_json(schema_path)
        # jsonschema.validators.Draft7Validator.check_schema raises on invalid schemas.
        jsonschema.validators.Draft7Validator.check_schema(schema)


def test_manifest_schema_accepts_minimal_manifest():
    schema = _load_json(SCHEMA_DIR / "manifest.schema.json")
    instance = {
        "doc_id": "sample",
        "source_file": "data/raw/HR/sample_raw.txt",
        "department": "HR",
        "status": "parsed",
        "timestamp": "2026-08-28T00:00:00Z",
        "parse_errors": [],
    }
    jsonschema.validate(instance=instance, schema=schema)


def test_pending_queue_fixture():
    schema = _load_json(SCHEMA_DIR / "pending_queue.schema.json")
    instance = [
        {
            "doc_id": "sample",
            "source_file": "data/raw/HR/sample_raw.txt",
            "department": "HR",
            "decision": "auto_process",
        }
    ]
    jsonschema.validate(instance=instance, schema=schema)


def test_status_template():
    schema = _load_json(SCHEMA_DIR / "status.schema.json")
    instance = _load_json(ROOT / "status" / "kb_status.json")
    jsonschema.validate(instance=instance, schema=schema)


def test_parsed_doc_fixture():
    schema = _load_json(SCHEMA_DIR / "parsed_doc.schema.json")
    instance = _load_json(FIXTURE_DIR / "sample_parsed.json")
    jsonschema.validate(instance=instance, schema=schema)


def test_chunks_fixture():
    schema = _load_json(SCHEMA_DIR / "chunks.schema.json")
    instance = _load_json(FIXTURE_DIR / "sample_chunks.json")
    jsonschema.validate(instance=instance, schema=schema)


def test_embeddings_manifest_schema():
    schema = _load_json(SCHEMA_DIR / "embeddings_manifest.schema.json")
    instance = {
        "doc_id": "sample",
        "source_file": "data/raw/HR/sample_raw.txt",
        "department": "HR",
        "status": "embedded",
        "timestamp": "2026-08-28T00:00:00Z",
        "parse_errors": [],
        "stage": "embedder",
        "embedding_model": "BAAI/bge-small-zh-v1.5",
        "embedding_dim": 512,
        "vector_count": 2,
        "chunk_order": [0, 1],
    }
    jsonschema.validate(instance=instance, schema=schema)


def test_pipeline_yaml_contains_required_keys():
    config = _load_yaml(ROOT / "config" / "pipeline.yaml")
    required_keys = {
        "watch_folders",
        "auto_process_rules",
        "chunking",
        "embedding",
        "vectorstore",
        "llm",
        "retrieval",
    }
    assert required_keys.issubset(config.keys())
    assert config["watch_folders"][0]["path"] == "data/raw/HR"


def test_golden_qa_yaml_is_loadable():
    qa = _load_yaml(ROOT / "tests" / "golden_qa.yaml")
    assert isinstance(qa, list)
    assert len(qa) == 3
    assert all("question" in item and "expected_doc_id" in item for item in qa)

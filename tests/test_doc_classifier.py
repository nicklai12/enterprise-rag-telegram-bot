"""Tests for doc_classifier.py."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import jsonschema
import pytest
import yaml

import doc_classifier

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schemas" / "pending_queue.schema.json"


def _write_config(tmp_path: pathlib.Path, allow_paths: list[str], deny_keywords: list[str]) -> pathlib.Path:
    config = {
        "watch_folders": [
            {"path": "data/raw/HR", "department": "HR", "access_level": "internal"}
        ],
        "auto_process_rules": {
            "allow_paths": allow_paths,
            "deny_keywords": deny_keywords,
        },
    }
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


def _make_raw_tree(tmp_path: pathlib.Path, files: list[str]) -> pathlib.Path:
    """Create files under a temporary data/raw tree; return the raw dir."""
    raw_dir = tmp_path / "data" / "raw"
    for rel_path in files:
        file_path = raw_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("dummy content", encoding="utf-8")
    return raw_dir


def test_deny_keyword_triggers_manual_review(tmp_path: pathlib.Path):
    """A filename containing a deny keyword is classified as needs_review."""
    config_path = _write_config(
        tmp_path, allow_paths=["data/raw/HR"], deny_keywords=["合約", "薪資"]
    )
    raw_dir = _make_raw_tree(tmp_path, ["HR/員工合約.pdf"])
    output_path = tmp_path / "pending_queue.json"

    queue = doc_classifier.run(config_path, output_path, raw_dir)

    assert len(queue) == 1
    assert queue[0]["decision"] == "needs_review"
    assert "合約" in queue[0]["reason"]


def test_file_outside_allow_paths_triggers_manual_review(tmp_path: pathlib.Path):
    """A file not under any allow_paths is classified as needs_review."""
    config_path = _write_config(
        tmp_path, allow_paths=["data/raw/HR"], deny_keywords=["合約", "薪資"]
    )
    raw_dir = _make_raw_tree(tmp_path, ["Finance/財報.xlsx"])
    output_path = tmp_path / "pending_queue.json"

    queue = doc_classifier.run(config_path, output_path, raw_dir)

    assert len(queue) == 1
    assert queue[0]["decision"] == "needs_review"
    assert "allow_paths" in queue[0]["reason"]


def test_clean_file_is_auto_processed(tmp_path: pathlib.Path):
    """A file under allow_paths and without deny keywords is auto_process."""
    config_path = _write_config(
        tmp_path, allow_paths=["data/raw/HR"], deny_keywords=["合約", "薪資"]
    )
    raw_dir = _make_raw_tree(tmp_path, ["HR/請假規則.txt"])
    output_path = tmp_path / "pending_queue.json"

    queue = doc_classifier.run(config_path, output_path, raw_dir)

    assert len(queue) == 1
    assert queue[0]["decision"] == "auto_process"
    assert queue[0]["department"] == "HR"


def test_output_validates_against_schema(tmp_path: pathlib.Path):
    """The generated pending_queue.json passes the schema validation."""
    config_path = _write_config(
        tmp_path,
        allow_paths=["data/raw/HR"],
        deny_keywords=["合約", "薪資"],
    )
    raw_dir = _make_raw_tree(
        tmp_path,
        [
            "HR/請假規則.txt",
            "HR/員工合約.pdf",
            "Finance/財報.xlsx",
        ],
    )
    output_path = tmp_path / "pending_queue.json"

    doc_classifier.run(config_path, output_path, raw_dir)

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    queue = json.loads(output_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=queue, schema=schema)

    decisions = {item["decision"] for item in queue}
    assert decisions == {"auto_process", "needs_review"}

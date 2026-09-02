"""Tests for parser.py."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import jsonschema

import parser


def _load_json(path: pathlib.Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _setup_doc(tmp_path: pathlib.Path, name: str, content: bytes) -> tuple[pathlib.Path, pathlib.Path]:
    raw_root = tmp_path
    source_file = f"data/raw/HR/{name}"
    abs_path = raw_root / source_file
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(content)
    parsed_dir = tmp_path / "data" / "parsed"
    return raw_root, parsed_dir


def test_successful_parse_writes_parsed_json_and_manifest(tmp_path: pathlib.Path):
    raw_root, parsed_dir = _setup_doc(
        tmp_path, "leave_rules.txt", "人力資源部請假規則\n\n一、特休。員工每滿六個月可享三日特休。\n".encode("utf-8")
    )

    manifest = parser.parse_document(
        source_file="data/raw/HR/leave_rules.txt",
        doc_id="data_raw_HR_leave_rules",
        department="HR",
        raw_root=raw_root,
        parsed_dir=parsed_dir,
    )

    assert manifest["status"] == "success"
    assert manifest["stage"] == "parser"
    assert manifest["parse_errors"] == []

    parsed = _load_json(parsed_dir / "data_raw_HR_leave_rules.json")
    assert parsed["doc_id"] == "data_raw_HR_leave_rules"
    assert parsed["source_file"] == "data/raw/HR/leave_rules.txt"
    assert parsed["department"] == "HR"
    assert len(parsed["elements"]) >= 1
    assert all(set(el.keys()) <= {"type", "text", "metadata"} for el in parsed["elements"])
    assert all(isinstance(el["type"], str) and el["text"] for el in parsed["elements"])

    manifest_on_disk = _load_json(parsed_dir / "data_raw_HR_leave_rules.manifest.json")
    assert manifest_on_disk == manifest


def test_outputs_validate_against_schemas(tmp_path: pathlib.Path):
    raw_root, parsed_dir = _setup_doc(
        tmp_path, "schema_check.txt", " schema 驗證用的測試文件。\n".encode("utf-8")
    )
    parser.parse_document(
        source_file="data/raw/HR/schema_check.txt",
        doc_id="data_raw_HR_schema_check",
        department="HR",
        raw_root=raw_root,
        parsed_dir=parsed_dir,
    )

    parsed_schema = _load_json(ROOT / "schemas" / "parsed_doc.schema.json")
    manifest_schema = _load_json(ROOT / "schemas" / "manifest.schema.json")
    jsonschema.validate(_load_json(parsed_dir / "data_raw_HR_schema_check.json"), parsed_schema)
    jsonschema.validate(
        _load_json(parsed_dir / "data_raw_HR_schema_check.manifest.json"), manifest_schema
    )


def test_corrupted_file_records_failed_manifest_without_raising(tmp_path: pathlib.Path):
    raw_root, parsed_dir = _setup_doc(tmp_path, "broken.pdf", b"%PDF-1.4 not a real pdf content")

    manifest = parser.parse_document(
        source_file="data/raw/HR/broken.pdf",
        doc_id="data_raw_HR_broken",
        department="HR",
        raw_root=raw_root,
        parsed_dir=parsed_dir,
    )

    assert manifest["status"] == "failed"
    assert len(manifest["parse_errors"]) == 1
    assert manifest["parse_errors"][0]
    assert not (parsed_dir / "data_raw_HR_broken.json").exists()
    manifest_on_disk = _load_json(parsed_dir / "data_raw_HR_broken.manifest.json")
    assert manifest_on_disk == manifest


def test_missing_file_records_failed_manifest(tmp_path: pathlib.Path):
    parsed_dir = tmp_path / "data" / "parsed"

    manifest = parser.parse_document(
        source_file="data/raw/HR/never_existed.pdf",
        doc_id="data_raw_HR_never_existed",
        department="HR",
        raw_root=tmp_path,
        parsed_dir=parsed_dir,
    )

    assert manifest["status"] == "failed"
    assert manifest["parse_errors"]


def test_unsupported_format_records_failed_manifest(tmp_path: pathlib.Path, capsys):
    """A corrupted document must not propagate: run() returns a failed manifest."""
    raw_root, parsed_dir = _setup_doc(tmp_path, "cli_broken.xlsx", b"PK\x03\x04 garbage")

    manifest = parser.parse_document(
        source_file="data/raw/HR/cli_broken.xlsx",
        doc_id="data_raw_HR_cli_broken",
        department="HR",
        raw_root=raw_root,
        parsed_dir=parsed_dir,
    )

    assert manifest["status"] == "failed"
    out = capsys.readouterr()
    assert out.err == ""


def test_resolve_doc_info_uses_pending_queue_then_classifier(tmp_path: pathlib.Path, monkeypatch):
    queue = [
        {
            "doc_id": "from_queue",
            "source_file": "data/raw/HR/in_queue.txt",
            "department": "HR",
            "decision": "auto_process",
        }
    ]
    queue_path = tmp_path / "pending_queue.json"
    queue_path.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(parser, "PENDING_QUEUE_PATH", queue_path)

    assert parser.resolve_doc_info("data/raw/HR/in_queue.txt") == ("from_queue", "HR")
    assert parser.resolve_doc_info("data/raw/HR/not_in_queue.txt") == (
        "data_raw_HR_not_in_queue",
        "HR",
    )

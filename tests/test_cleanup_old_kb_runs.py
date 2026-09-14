"""Tests for cleanup_old_kb_runs.py (all against a local ephemeral Chroma client).

chromadb 1.x EphemeralClient instances share one in-process backend, so
each test uses uuid-suffixed collection names via a temp pipeline.yaml
(same pattern as tests/test_bot.py).
"""
import pathlib
import sys
import uuid

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chromadb

import cleanup_old_kb_runs

CONFIG_PATH = ROOT / "config" / "pipeline.yaml"
POINTER_ID = "active_pointer"

ACTIVE = "20260914-124536"
OLD = "20260913-205903"
OLDER = "20260912-204319"


def _write_config(tmp_path: pathlib.Path, control_name: str, data_name: str) -> pathlib.Path:
    """Copy pipeline.yaml with unique collection names for this test."""
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["vectorstore"]["control_collection_name"] = control_name
    cfg["vectorstore"]["data_collection_name"] = data_name
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


def _seed(client: chromadb.EphemeralClient, with_pointer: bool = True):
    """Seed control pointer (optional) + data records across three versions."""
    control_name = f"company_kb_control_{uuid.uuid4().hex}"
    data_name = f"company_kb_data_{uuid.uuid4().hex}"
    control = client.create_collection(control_name)
    if with_pointer:
        control.upsert(
            ids=[POINTER_ID],
            metadatas=[{"active_kb_run_id": ACTIVE}],
            documents=["active pointer"],
            embeddings=[[1.0, 0.0]],
        )
    data = client.create_collection(data_name)
    versions = {
        ACTIVE: ["a0", "a1"],
        OLD: ["b0"],
        OLDER: ["c0", "c1", "c2"],
    }
    data.upsert(
        ids=[f"{kb}_{i}" for kb, items in versions.items() for i in items],
        metadatas=[
            {"kb_run_id": kb, "doc_id": f"doc_{kb}", "chunk_index": int(i[1:])}
            for kb, items in versions.items()
            for i in items
        ],
        documents=["text"] * sum(len(v) for v in versions.values()),
        embeddings=[[1.0, 0.0]] * sum(len(v) for v in versions.values()),
    )
    return control_name, data_name


def _data_count(client: chromadb.EphemeralClient, data_name: str) -> int:
    return client.get_collection(data_name).count()


def test_dry_run_reports_without_deleting(tmp_path):
    """--dry-run lists per-version actions but leaves every record in place."""
    client = chromadb.EphemeralClient()
    control_name, data_name = _seed(client)
    config_path = _write_config(tmp_path, control_name, data_name)
    report = cleanup_old_kb_runs.run(dry_run=True, config_path=config_path, client=client)
    assert report["status"] == "dry_run"
    assert report["deleted_records"] == 0
    assert report["would_delete_records"] == 4  # OLD(1) + OLDER(3)
    assert report["versions"][ACTIVE]["action"] == "kept"
    assert report["versions"][OLD]["action"] == "would_delete"
    assert _data_count(client, data_name) == 6


def test_deletes_stale_versions_keeps_active(tmp_path):
    """Stale kb_run_id records are removed; the active version is untouched."""
    client = chromadb.EphemeralClient()
    control_name, data_name = _seed(client)
    config_path = _write_config(tmp_path, control_name, data_name)
    report = cleanup_old_kb_runs.run(config_path=config_path, client=client)
    assert report["status"] == "cleaned"
    assert report["deleted_records"] == 4
    assert report["active_kb_run_id"] == ACTIVE
    assert _data_count(client, data_name) == 2
    remaining = client.get_collection(data_name).get(include=["metadatas"])
    assert all(m["kb_run_id"] == ACTIVE for m in remaining["metadatas"])


def test_keep_flag_preserves_additional_version(tmp_path):
    """--keep retains a non-active version alongside the active one."""
    client = chromadb.EphemeralClient()
    control_name, data_name = _seed(client)
    config_path = _write_config(tmp_path, control_name, data_name)
    report = cleanup_old_kb_runs.run(
        keep=[OLDER], config_path=config_path, client=client
    )
    assert report["kept_kb_run_ids"] == sorted([ACTIVE, OLDER])
    assert report["deleted_records"] == 1  # only OLD
    remaining = client.get_collection(data_name).get(include=["metadatas"])
    kept = {m["kb_run_id"] for m in remaining["metadatas"]}
    assert kept == {ACTIVE, OLDER}


def test_refuses_to_wipe_without_pointer_or_keep(tmp_path):
    """No active pointer and no --keep → error, nothing deleted."""
    client = chromadb.EphemeralClient()
    control_name, data_name = _seed(client, with_pointer=False)
    config_path = _write_config(tmp_path, control_name, data_name)
    with pytest.raises(RuntimeError, match="refusing to wipe"):
        cleanup_old_kb_runs.run(config_path=config_path, client=client)
    assert _data_count(client, data_name) == 6


def test_deletes_records_missing_kb_run_id_metadata(tmp_path):
    """Records without kb_run_id metadata are stale garbage and get removed."""
    client = chromadb.EphemeralClient()
    control_name, data_name = _seed(client)
    config_path = _write_config(tmp_path, control_name, data_name)
    data = client.get_collection(data_name)
    data.upsert(
        ids=["legacy_no_run_id"],
        metadatas=[{"doc_id": "legacy"}],
        documents=["text"],
        embeddings=[[1.0, 0.0]],
    )
    report = cleanup_old_kb_runs.run(config_path=config_path, client=client)
    assert report["versions"]["(no kb_run_id)"]["action"] == "deleted"
    assert _data_count(client, data_name) == 2

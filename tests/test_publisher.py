"""Tests for publisher.py.

All tests use a local ephemeral ChromaDB instance — no Chroma Cloud calls.
Covers the two publisher behaviors from spec §5.8:
1. publishing updates the active_pointer record to the candidate kb_run_id;
2. a failed write exits non-zero and leaves the previous pointer untouched
   (no partial/dirty writes).
"""
import json
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chromadb
import yaml

import publisher

CONFIG_PATH = ROOT / "config" / "pipeline.yaml"
CONTROL_COLLECTION = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))[
    "vectorstore"
]["control_collection_name"]
POINTER_ID = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))[
    "vectorstore"
]["control_pointer_id"]

CANDIDATE_KB_RUN_ID = "20260906-130000"
PREVIOUS_KB_RUN_ID = "20260905-090000"
PREVIOUS_PUBLISHED_AT = "2026-09-05T09:00:00+00:00"


def _build_control_client(with_existing_pointer: bool = True):
    """Ephemeral client whose control collection may hold a prior pointer.

    ``run()`` asks for the collection by its configured name; the ephemeral
    system DB is shared in-process, so we hand back a uniquely named
    collection instead of letting it create/attach to the plain name.
    """
    client = chromadb.EphemeralClient()
    collection = client.create_collection(f"{CONTROL_COLLECTION}_{uuid.uuid4().hex}")
    if with_existing_pointer:
        collection.upsert(
            ids=[POINTER_ID],
            metadatas=[
                {
                    "active_kb_run_id": PREVIOUS_KB_RUN_ID,
                    "published_at": PREVIOUS_PUBLISHED_AT,
                }
            ],
            documents=[f"active pointer -> kb_run_id {PREVIOUS_KB_RUN_ID}"],
        )

    class ControlClient:
        def get_or_create_collection(self, name):
            assert name == CONTROL_COLLECTION
            return collection

    return ControlClient(), collection


def test_publish_updates_active_pointer(tmp_path):
    """After publish, control collection active_pointer holds the candidate."""
    client, collection = _build_control_client()
    before = collection.get(ids=[POINTER_ID], include=["metadatas"])
    assert before["metadatas"][0]["active_kb_run_id"] == PREVIOUS_KB_RUN_ID

    manifest = publisher.run(
        kb_run_id=CANDIDATE_KB_RUN_ID,
        config_path=CONFIG_PATH,
        client=client,
    )

    after = collection.get(ids=[POINTER_ID], include=["metadatas"])
    metadata = after["metadatas"][0]
    assert metadata["active_kb_run_id"] == CANDIDATE_KB_RUN_ID
    assert metadata["published_at"] != PREVIOUS_PUBLISHED_AT
    assert manifest["status"] == "published"
    assert manifest["collection_name"] == CONTROL_COLLECTION
    assert manifest["pointer_id"] == POINTER_ID
    assert manifest["active_kb_run_id"] == CANDIDATE_KB_RUN_ID
    assert manifest["published_at"] == metadata["published_at"]


def test_publish_creates_pointer_when_absent(tmp_path):
    """First-ever publish: the fixed-id record is created, not required."""
    client, collection = _build_control_client(with_existing_pointer=False)

    publisher.run(
        kb_run_id=CANDIDATE_KB_RUN_ID,
        config_path=CONFIG_PATH,
        client=client,
    )

    after = collection.get(ids=[POINTER_ID], include=["metadatas"])
    assert after["metadatas"][0]["active_kb_run_id"] == CANDIDATE_KB_RUN_ID


def test_failed_write_keeps_previous_pointer_and_exits_nonzero(
    tmp_path, monkeypatch, capsys
):
    """Upsert failure → non-zero exit, and the old pointer is untouched."""
    client, collection = _build_control_client()

    class FailingCollection:
        def upsert(self, **kwargs):
            raise RuntimeError("simulated control collection write failure")

    monkeypatch.setattr(
        client, "get_or_create_collection", lambda name: FailingCollection()
    )
    monkeypatch.setattr(publisher, "connect_chroma", lambda: client)
    monkeypatch.setattr(
        "sys.argv",
        ["publisher.py", "--kb-run-id", CANDIDATE_KB_RUN_ID],
    )

    try:
        publisher.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("publisher.main() should have exited non-zero")
    assert "publisher failed" in capsys.readouterr().err

    after = collection.get(ids=[POINTER_ID], include=["metadatas"])
    assert after["metadatas"][0]["active_kb_run_id"] == PREVIOUS_KB_RUN_ID
    assert after["metadatas"][0]["published_at"] == PREVIOUS_PUBLISHED_AT


def test_run_manifest_is_json_serializable(tmp_path):
    """The run() manifest prints cleanly as JSON (CLI output path)."""
    client, _ = _build_control_client()
    manifest = publisher.run(
        kb_run_id=CANDIDATE_KB_RUN_ID,
        config_path=CONFIG_PATH,
        client=client,
    )
    json.dumps(manifest, ensure_ascii=False)

"""cleanup_old_kb_runs.py: delete stale kb_run_id records from the data collection.

做法 B keeps every candidate version in the data collection, so old
``kb_run_id`` records accumulate until the Chroma Cloud record quota is
exhausted (Starter: 300 records), blocking new upserts (issue #25).

This is a MANUAL utility, not a pipeline stage: it deletes every record
whose ``kb_run_id`` is neither the published active version (read from the
control collection pointer, per spec §4.2) nor explicitly kept via
``--keep``.

Safety rails:
- ``--dry-run`` reports what would be deleted without deleting.
- If no active pointer exists and no ``--keep`` is given, nothing is
  deleted (refuses to wipe the whole collection). Use ``--all`` to
  explicitly wipe every record — e.g. when nothing was ever published
  and all records are leftovers from failed runs.

Connection: ``chromadb.CloudClient`` with credentials from the environment
variables ``CHROMA_API_KEY`` / ``CHROMA_TENANT`` / ``CHROMA_DATABASE``
(same as indexer.py).

Usage:
    python cleanup_old_kb_runs.py --dry-run              # report only
    python cleanup_old_kb_runs.py                        # delete stale versions
    python cleanup_old_kb_runs.py --keep 20260914-124536 # also keep this version
    python cleanup_old_kb_runs.py --all                  # wipe every record
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "pipeline.yaml"

# Chroma Cloud「Maximum number of results returned」上限為 300
# （https://docs.trychroma.com/cloud/quotas-limits），get 需分頁讀完（issue #25）。
GET_PAGE_SIZE = 250

DELETE_BATCH_SIZE = 100


def load_config(path: pathlib.Path = CONFIG_PATH) -> dict[str, Any]:
    """Load pipeline configuration from YAML."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def connect_chroma() -> Any:
    """Connect to Chroma Cloud using credentials from environment variables."""
    import chromadb

    return chromadb.CloudClient(
        api_key=os.environ["CHROMA_API_KEY"],
        tenant=os.environ["CHROMA_TENANT"],
        database=os.environ["CHROMA_DATABASE"],
    )


def read_active_kb_run_id(client: Any, config: dict[str, Any]) -> str | None:
    """Read the published active_kb_run_id from the control collection pointer."""
    vectorstore = config.get("vectorstore", {})
    collection_name = vectorstore.get("control_collection_name", "company_kb_control")
    pointer_id = vectorstore.get("control_pointer_id", "active_pointer")
    try:
        collection = client.get_collection(collection_name)
        found = collection.get(ids=[pointer_id], include=["metadatas"])
    except Exception:
        return None
    if not found["ids"]:
        return None
    return found["metadatas"][0].get("active_kb_run_id")


def group_ids_by_kb_run_id(collection: Any) -> dict[str, list[str]]:
    """Group all record ids in the collection by their metadata kb_run_id.

    Paginates: Chroma Cloud caps each read at 300 results, so a large
    collection returns incomplete data without limit/offset (issue #25).
    """
    found_ids: list[str] = []
    found_metadatas: list[Any] = []
    offset = 0
    while True:
        found = collection.get(
            include=["metadatas"], limit=GET_PAGE_SIZE, offset=offset
        )
        found_ids.extend(found["ids"])
        found_metadatas.extend(found["metadatas"])
        if len(found["ids"]) < GET_PAGE_SIZE:
            break
        offset += GET_PAGE_SIZE
    groups: dict[str, list[str]] = {}
    for record_id, metadata in zip(found_ids, found_metadatas):
        key = (metadata or {}).get("kb_run_id") or "(no kb_run_id)"
        groups.setdefault(key, []).append(record_id)
    return groups


def run(
    keep: list[str] | None = None,
    dry_run: bool = False,
    all_records: bool = False,
    config_path: pathlib.Path = CONFIG_PATH,
    client: Any = None,
) -> dict[str, Any]:
    """Delete stale kb_run_id records, keeping the active version and --keep list."""
    config = load_config(config_path)
    vectorstore = config.get("vectorstore", {})
    data_collection_name = vectorstore.get("data_collection_name", "company_kb_data")
    if client is None:
        client = connect_chroma()

    active_kb_run_id = None if all_records else read_active_kb_run_id(client, config)
    keep_ids = set(keep or [])
    if active_kb_run_id:
        keep_ids.add(active_kb_run_id)
    if not keep_ids and not all_records:
        raise RuntimeError(
            "no active pointer and no --keep given; refusing to wipe the whole "
            "collection (pass --keep <kb_run_id> to keep at least one version, "
            "or --all to wipe every record explicitly)"
        )

    collection = client.get_collection(data_collection_name)
    groups = group_ids_by_kb_run_id(collection)

    versions: dict[str, dict[str, Any]] = {}
    deleted_total = 0
    for kb_run_id in sorted(groups):
        ids = groups[kb_run_id]
        kept = kb_run_id in keep_ids
        versions[kb_run_id] = {
            "records": len(ids),
            "action": "kept" if kept else ("would_delete" if dry_run else "deleted"),
        }
        if kept:
            continue
        for start in range(0, len(ids), DELETE_BATCH_SIZE):
            batch = ids[start : start + DELETE_BATCH_SIZE]
            if not dry_run:
                collection.delete(ids=batch)
            deleted_total += len(batch)

    return {
        "status": "dry_run" if dry_run else "cleaned",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data_collection_name": data_collection_name,
        "active_kb_run_id": active_kb_run_id,
        "kept_kb_run_ids": sorted(keep_ids),
        "deleted_records": 0 if dry_run else deleted_total,
        "would_delete_records": deleted_total if dry_run else 0,
        "versions": versions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        action="append",
        default=None,
        metavar="KB_RUN_ID",
        help="kb_run_id to keep (repeatable); the active version is always kept.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be deleted without deleting.",
    )
    parser.add_argument(
        "--all",
        dest="all_records",
        action="store_true",
        help="Wipe every record in the data collection (explicit opt-in).",
    )
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=CONFIG_PATH,
        help="Path to pipeline.yaml.",
    )
    args = parser.parse_args()
    try:
        report = run(
            keep=args.keep,
            dry_run=args.dry_run,
            all_records=args.all_records,
            config_path=args.config,
        )
    except Exception as exc:
        print(f"cleanup failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""doc_classifier.py: rule-based document classifier for the KB pipeline.

Reads ``config/pipeline.yaml`` and scans ``data/raw/**``. Each file is
tagged as ``auto_process`` or ``needs_review`` based on the
``auto_process_rules`` allow-list and deny-list. No LLM is used.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "pipeline.yaml"
OUTPUT_PATH = ROOT / "pending_queue.json"
RAW_DIR = ROOT / "data" / "raw"


def load_config(path: pathlib.Path = CONFIG_PATH) -> dict[str, Any]:
    """Load pipeline configuration from YAML."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _doc_id(source_file: str) -> str:
    """Derive a stable doc_id from the repo-relative source path."""
    path = pathlib.PurePath(source_file)
    without_suffix = path.with_suffix("")
    return str(without_suffix).replace("\\", "/").replace("/", "_")


def _department(source_file: str, watch_folders: list[dict[str, Any]]) -> str:
    """Map a source file to its department using watch_folders paths."""
    source = pathlib.PurePath(source_file)
    for folder in watch_folders:
        folder_path = folder.get("path", "")
        if source.is_relative_to(folder_path):
            return folder.get("department", "unknown")
    return "unknown"


def classify(
    source_file: str,
    rules: dict[str, Any],
    watch_folders: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify a single file path.

    Rules (in priority order):
      1. If the file path/name contains any deny_keywords -> needs_review.
      2. If the file is not under any allow_paths -> needs_review.
      3. Otherwise -> auto_process.
    """
    watch_folders = watch_folders or []
    source_file = source_file.replace("\\", "/")

    allow_paths = rules.get("allow_paths", [])
    deny_keywords = rules.get("deny_keywords", [])

    matched_deny = next((kw for kw in deny_keywords if kw in source_file), None)
    is_allowed = any(
        pathlib.PurePath(source_file).is_relative_to(p) for p in allow_paths
    )

    item: dict[str, Any] = {
        "doc_id": _doc_id(source_file),
        "source_file": source_file,
        "department": _department(source_file, watch_folders),
    }

    if matched_deny:
        item["decision"] = "needs_review"
        item["reason"] = f"命中黑名單關鍵字：{matched_deny}"
    elif not is_allowed:
        item["decision"] = "needs_review"
        item["reason"] = "不在 allow_paths 白名單內"
    else:
        item["decision"] = "auto_process"

    return item


def scan_raw_files(raw_dir: pathlib.Path) -> list[str]:
    """Return repo-relative POSIX paths for every file under ``raw_dir``."""
    repo_root = raw_dir.resolve().parent.parent
    files: list[str] = []
    for path in sorted(raw_dir.rglob("*")):
        if path.is_file():
            rel = path.resolve().relative_to(repo_root).as_posix()
            files.append(rel)
    return files


def run(
    config_path: pathlib.Path = CONFIG_PATH,
    output_path: pathlib.Path = OUTPUT_PATH,
    raw_dir: pathlib.Path = RAW_DIR,
) -> list[dict[str, Any]]:
    """Execute the classifier and write ``pending_queue.json``."""
    config = load_config(config_path)
    rules = config.get("auto_process_rules", {})
    watch_folders = config.get("watch_folders", [])

    queue = [
        classify(source_file, rules, watch_folders)
        for source_file in scan_raw_files(raw_dir)
    ]

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

    return queue


if __name__ == "__main__":
    run()

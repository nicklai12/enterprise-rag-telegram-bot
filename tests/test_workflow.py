"""Tests for .github/workflows/kb-pipeline.yml (spec §8 GitHub Actions SOP).

These are offline structural checks: the workflow cannot be executed against
real Chroma Cloud in CI (hard rule: no real requests in automated tests), so
we assert the control-flow contract that makes "verify 失敗 → publisher 被跳過"
and "concurrency lock 生效" hold on GitHub Actions:

- daily cron trigger + manual dispatch
- concurrency group configured (overlapping runs wait)
- steps follow the spec §8 order
- publisher step is gated on ``success()``
- kb_run_id is generated once and shared by indexer / verify / publisher
- Chroma credentials come from GitHub Secrets, never hard-coded
"""
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "kb-pipeline.yml"


def _workflow() -> dict:
    with WORKFLOW_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _on(wf: dict) -> dict:
    # PyYAML 1.1 parses the bare key ``on:`` as boolean True; GitHub parses it
    # as the string "on". Accept either so the test reflects the real file.
    return wf.get("on") or wf.get(True)


def _steps(wf: dict) -> list[dict]:
    return [s for s in wf["jobs"]["pipeline"]["steps"] if isinstance(s, dict) and "run" in s]


def _step(wf: dict, name: str) -> dict:
    for step in wf["jobs"]["pipeline"]["steps"]:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    raise AssertionError(f"step {name!r} not found in workflow")


def test_schedule_cron_runs_daily():
    crons = _on(_workflow())["schedule"]
    assert len(crons) == 1
    fields = crons[0]["cron"].split()
    assert len(fields) == 5
    assert fields[2] == "*" and fields[3] == "*"  # every day, every month


def test_workflow_dispatch_available_for_manual_runs():
    assert "workflow_dispatch" in _on(_workflow())


def test_concurrency_lock_configured_to_wait():
    conc = _workflow()["concurrency"]
    assert conc["group"]
    # false = 第二個 run 等待第一個結束（不重疊執行）
    assert conc["cancel-in-progress"] is False


def test_steps_follow_spec_section_8_order():
    names = [s["name"] for s in _steps(_workflow())]
    keywords = ["classifier", "watcher", "parser", "chunker", "embedder",
                "indexer", "verify", "publisher", "commit"]
    positions = [next(i for i, n in enumerate(names) if kw in n) for kw in keywords]
    assert positions == sorted(positions), names


def test_publisher_is_gated_on_success():
    """verify 失敗 → success() 為 false → publisher 顯示 skipped。"""
    publisher_if = _step(_workflow(), "publisher")["if"]
    assert "success()" in publisher_if


def test_kb_run_id_generated_once_and_shared():
    wf = _workflow()
    assert "kb_run_id" in _step(wf, "Generate kb_run_id")["run"]
    run_id_ref = "${{ steps.run_id.outputs.kb_run_id }}"
    for name in ("indexer", "verify", "publisher"):
        assert run_id_ref in _step(wf, name)["run"]
    # verify 必須針對「候選 kb_run_id」審計
    assert "--kb-run-id" in _step(wf, "verify")["run"]


def test_chroma_credentials_from_secrets_only():
    env = _workflow()["env"]
    for var in ("CHROMA_API_KEY", "CHROMA_TENANT", "CHROMA_DATABASE"):
        assert env[var] == "${{ secrets." + var + " }}"
    # 整份 workflow 不得出現硬編碼金鑰樣式的值（ Secrets 參照除外）
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert not re.search(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}", text)


def test_system_dependencies_installed():
    """unstructured（poppler）與 torch/PIL（libGL）需先裝系統套件。"""
    run = _step(_workflow(), "Install system dependencies")["run"]
    assert "poppler-utils" in run
    assert "libgl1" in run


def test_commit_step_records_status_even_on_failure():
    commit_if = _step(_workflow(), "commit kb_status.json")["if"]
    assert "always()" in commit_if
    assert "status/kb_status.json" in _step(_workflow(), "commit kb_status.json")["run"]


def test_doc_id_derived_by_doc_classifier_not_shell():
    """All per-document stages derive doc_id via ``doc_classifier.py --doc-id``.

    Regression test for issue #15: shell ``${f%.*}`` and Python
    ``PurePath.with_suffix`` diverge on dotfiles (e.g. .gitkeep), which
    crashed the chunker with a FileNotFoundError on the parsed JSON.
    """
    for name in ("chunker", "embedder", "indexer"):
        run = _step(_workflow(), name)["run"]
        assert "doc_classifier.py --doc-id" in run, name
        assert "${f%.*}" not in run, name

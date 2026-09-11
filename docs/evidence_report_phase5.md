# Phase 5 證據報告 — kb-pipeline workflow + 部署說明

- 日期：2026-09-11
- Branch：`phase5-workflow`
- PR：https://github.com/nicklai12/enterprise-rag-telegram-bot/pull/12
- 依據：spec.md §8（GitHub Actions SOP）、§4.1（kb_run_id）、system-map.md（密鑰管理、執行環境）

## 假設（與 PR 描述相同，請確認）

1. **`indexer.py` 新增 `--kb-run-id`**：既有 CLI 每次呼叫產生新 kb_run_id，批次多份文件會違反 spec §4.1「每次 pipeline 執行產生一個 kb_run_id」。workflow 產生一次並傳給 indexer/verify/publisher；`run()` 本來就接受此參數，CLI 僅暴露它（預設 None 行為不變）。
2. **加 `workflow_dispatch`**：供驗收時手動觸發（cron 無法手動跑）。
3. **concurrency 採等待（`cancel-in-progress: false`）**：DoD 接受「等待或取消」之一，發布類 pipeline 等待較安全。
4. **watcher batch 為空時跳過 parser～commit**：否則每日 cron 在無新文件時 verify 必敗、天天誤報。
5. **commit 範圍外加 `status/watcher_state.json`**：否則跨 run 失去 watcher 狀態，每日重複處理全部文件。spec §8 步驟 9 只寫 kb_status.json，特此標註。
6. **README.md 既有未提交內容一併提交**（自 initial commit 後未入版控），本次任務要求的新增段落為「部署說明」。

## 做了什麼

1. 新增 `.github/workflows/kb-pipeline.yml`：
   - `on.schedule: "17 23 * * *"`（每日一次，UTC 23:17）+ `workflow_dispatch`
   - `concurrency: { group: kb-pipeline, cancel-in-progress: false }`
   - `env` 注入 `CHROMA_API_KEY / CHROMA_TENANT / CHROMA_DATABASE`（`${{ secrets.* }}`）
   - 步驟依序：Install deps → Generate kb_run_id → classifier → watcher → parser → chunker → embedder → indexer → verify → publisher → commit kb_status.json
   - publisher：`if: ${{ success() && steps.watcher.outputs.batch != '[]' }}` → verify 失敗即 **skipped**
   - commit：`if: ${{ always() && ... }}`（verify 失敗也會把 failed 紀錄寫進 kb_status.json 看板）
   - bash 預設 `-eo pipefail`：loop 內任一支程式 exit 非 0 → 該步失敗 → 後續步驟全部 skipped
2. `indexer.py`：argparse 新增 `--kb-run-id`（手術式兩行）。
3. `tests/test_workflow.py`（8 項）、`tests/test_indexer.py` 新增 1 項。
4. `README.md`：新增「部署說明（GitHub Actions + Render）」段落。

## 怎麼驗證（實際輸出）

### 1. 結構測試（本次新增的 workflow 測試）

```
$ python -m pytest tests/test_workflow.py tests/test_indexer.py -v
tests/test_workflow.py::test_schedule_cron_runs_daily PASSED
tests/test_workflow.py::test_workflow_dispatch_available_for_manual_runs PASSED
tests/test_workflow.py::test_concurrency_lock_configured_to_wait PASSED
tests/test_workflow.py::test_steps_follow_spec_section_8_order PASSED
tests/test_workflow.py::test_publisher_is_gated_on_success PASSED
tests/test_workflow.py::test_kb_run_id_generated_once_and_shared PASSED
tests/test_workflow.py::test_chroma_credentials_from_secrets_only PASSED
tests/test_workflow.py::test_commit_step_records_status_even_on_failure PASSED
tests/test_indexer.py::test_ephemeral_write_then_query_by_kb_run_id PASSED
tests/test_indexer.py::test_two_kb_run_ids_for_same_doc_coexist PASSED
tests/test_indexer.py::test_connection_failure_exits_nonzero PASSED
tests/test_indexer.py::test_missing_env_credentials_exits_nonzero PASSED
tests/test_indexer.py::test_write_failure_exits_nonzero PASSED
tests/test_indexer.py::test_new_kb_run_id_format PASSED
tests/test_indexer.py::test_cli_kb_run_id_flag PASSED
15 passed
```

### 2. 全量測試（無回歸）

```
$ python -m pytest tests/
65 passed, 1 failed
```

唯一失敗 `tests/test_schemas.py::test_golden_qa_yaml_is_loadable` 為**既有問題**（phase 3c / phase 4 報告已記錄：golden_qa.yaml 現有 14 題、該測試斷言 3 題），與本相位無關，依硬性規則 5 未動。

### 3. workflow 內 doc_id 推導與 classifier 一致（實際輸出）

workflow 用 bash 從 source_file 推 doc_id（`"${f%.*}"` + `/`→`_`），與 `doc_classifier._doc_id()` 比對：

```
bash  : data_raw_HR_請假辦法
python: data_raw_HR_請假辦法
```

### 4. verify 失敗 → publisher skipped 的機制驗證

- 結構面：`test_publisher_is_gated_on_success` 斷言 publisher step 的 `if` 含 `success()`；步驟順序測試斷言 verify 在 publisher 之前。
- 語意面：GitHub Actions 預設「任一步驟失敗 → 之後所有未標 `if: always()` 的步驟標記為 **skipped**」，publisher 的條件使其在 verify 失敗時必為 skipped（不會 failed、不會被執行）。
- shell 面：run 步驟使用 `bash -eo pipefail`，loop 內 `python verify_index.py` 以 exit code 1 回報（verify_index.py `main()` 回傳 `0 if report["passed"] else 1`），步驟正確失敗而不被吞掉。

## DoD 對照

- [ ] **故意讓 verify 失敗 → publisher 顯示 skipped**：workflow 控制流已用結構測試鎖定（publisher `if: success()`、verify 在前）。**但無法在本環境產生真實 run 記錄**：`workflow_dispatch` 需 workflow 存在於 default branch 才能觸發，且 indexer/verify 需真實 Chroma 憑證（硬性規則禁止對正式 Chroma Cloud 自動化測試）。**待 PR 合併、你設定好三個 CHROMA_* Secrets 後**，到 Actions → kb-pipeline → Run workflow 即可實際觀察；若要強制 verify 失敗，可暫時改 `tests/golden_qa.yaml` 的 `expected_doc_id` 為不存在值再手動觸發。
- [~] **兩個 workflow 同時觸發 → 第二個等待或取消**：`concurrency.group: kb-pipeline` + `cancel-in-progress: false` 已由結構測試斷言（第二個 run 會 pending 等待第一個結束）。同樣需合併後以兩次快速手動觸發實際觀察 run 記錄。
- [ ] **附「verify 失敗 → publisher 被跳過」的 run 截圖/log**：受限同上，需合併 + Secrets 設定後由你觸發（或授權我以你的 Secrets 觸發，但這會對正式 Chroma Cloud 寫入資料，違反硬性規則，故不執行）。

## 給你的下一步（人工驗收）

1. 合併 PR #12。
2. 設定 GitHub Secrets：`CHROMA_API_KEY`、`CHROMA_TENANT`、`CHROMA_DATABASE`（名稱見 README「部署說明」）。
3. Actions → kb-pipeline → Run workflow 觀察一次完整 run。
4. （可選）驗證 verify 失敗路徑：暫時把 `tests/golden_qa.yaml` 某題 `expected_doc_id` 改成亂值 → 手動觸發 → 確認 publisher 步驟顯示 **skipped**、kb_status.json 被 commit 回 repo 且記錄 status=failed → 還原 golden_qa.yaml。

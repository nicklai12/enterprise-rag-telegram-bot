# Phase 1b 證據報告 — doc_watcher.py

## 1. 本次實作內容

- 新增 `doc_watcher.py`：
  - 讀取 `pending_queue.json`，只處理 `decision == "auto_process"` 的項目。
  - 以 **檔案內容 md5 hash** 判斷新增/變更；同時把「上次因 20 份上限未輪到的檔案」視為仍待處理。
  - 單次輸出上限 **20 份**；超過的留到下次執行，不會被丟棄。
  - 輸出為本次待處理的 `source_file` 路徑清單（同時列印 JSON 到 stdout）。
  - 狀態持久化在 `status/watcher_state.json`，記錄每個 `doc_id` 的 hash 與最後處理時間。
- 新增 `tests/test_doc_watcher.py`：覆蓋上限、留到下次、已處理未變更不重複、變更後重新處理、只選 auto 項目等情境。

## 2. 實作假設與解讀（請你確認）

| 項目 | 本實作選擇 | 其他可能解讀 |
|---|---|---|
| 變更偵測方式 | 使用 **md5 content hash**。理由：hash 比 mtime 更能真實反映內容是否改變，不會因人為 touch 或 git 還原導致誤判。 | 使用 mtime，實作較簡單但可能漏判或誤判。 |
| 上限未輪到的檔案如何標記 | 狀態中記錄 hash，但 `last_processed_at` 為 `null`，因此下次仍會被選到。 | 也可以用獨立的 `pending_ids` 清單，但會與 hash 狀態分開維護。 |
| 輸出格式 | 只輸出 `source_file` 路徑清單（`list[str]`），供 parser.py 逐檔處理。 | 也可以輸出完整 pending queue 子集，但 spec 只要求路徑清單。 |
| 狀態檔位置 | `status/watcher_state.json`。spec 未指定 watcher 狀態位置，放在 `status/` 與 `kb_status.json` 同目錄，便於 pipeline 統一管理。 | 也可以放在 `data/watcher_state.json` 或記憶體傳遞。 |
| 批次內排序 | 依 `source_file` 字典序排序後取前 20，讓每次執行結果穩定、可預測。 | 也可以依 queue 原始順序，但原始順序可能受檔案系統影響。 |

## 3. 驗證方式

### 3.1 單元測試

```bash
pytest tests/test_doc_watcher.py -v
```

實際輸出：

```text
============================= test session starts ==============================
platform linux -- Python 3.12.1, pytest-7.4.4, pluggy-1.6.0 -- /home/codespace/.python/current/bin/python
cachedir: .pytest_cache
rootdir: /workspaces/enterprise-rag-telegram-bot
plugins: anyio-4.12.1
collecting ... collected 5 items

tests/test_doc_watcher.py::test_only_auto_items_are_selected PASSED      [ 20%]
tests/test_doc_watcher.py::test_batch_capped_at_twenty_and_remaining_next_run PASSED [ 40%]
tests/test_doc_watcher.py::test_processed_unchanged_files_not_reoutput PASSED [ 60%]
tests/test_doc_watcher.py::test_changed_file_is_reprocessed PASSED       [ 80%]
tests/test_doc_watcher.py::test_run_returns_source_file_paths PASSED     [100%]

============================== 5 passed in 0.06s ===============================
```

### 3.2 25 → 20 上限與剩餘 5 份實際執行結果

建立 25 份假檔案與對應的 `pending_queue.json`：

```text
data/raw/HR/doc_00.txt
data/raw/HR/doc_01.txt
data/raw/HR/doc_02.txt
data/raw/HR/doc_03.txt
data/raw/HR/doc_04.txt
data/raw/HR/doc_05.txt
data/raw/HR/doc_06.txt
data/raw/HR/doc_07.txt
data/raw/HR/doc_08.txt
data/raw/HR/doc_09.txt
data/raw/HR/doc_10.txt
data/raw/HR/doc_11.txt
data/raw/HR/doc_12.txt
data/raw/HR/doc_13.txt
data/raw/HR/doc_14.txt
data/raw/HR/doc_15.txt
data/raw/HR/doc_16.txt
data/raw/HR/doc_17.txt
data/raw/HR/doc_18.txt
data/raw/HR/doc_19.txt
data/raw/HR/doc_20.txt
data/raw/HR/doc_21.txt
data/raw/HR/doc_22.txt
data/raw/HR/doc_23.txt
data/raw/HR/doc_24.txt
```

第一次執行輸出（20 份）：

```json
[
  "data/raw/HR/doc_00.txt",
  "data/raw/HR/doc_01.txt",
  "data/raw/HR/doc_02.txt",
  "data/raw/HR/doc_03.txt",
  "data/raw/HR/doc_04.txt",
  "data/raw/HR/doc_05.txt",
  "data/raw/HR/doc_06.txt",
  "data/raw/HR/doc_07.txt",
  "data/raw/HR/doc_08.txt",
  "data/raw/HR/doc_09.txt",
  "data/raw/HR/doc_10.txt",
  "data/raw/HR/doc_11.txt",
  "data/raw/HR/doc_12.txt",
  "data/raw/HR/doc_13.txt",
  "data/raw/HR/doc_14.txt",
  "data/raw/HR/doc_15.txt",
  "data/raw/HR/doc_16.txt",
  "data/raw/HR/doc_17.txt",
  "data/raw/HR/doc_18.txt",
  "data/raw/HR/doc_19.txt"
]
```

第二次執行輸出（剩餘 5 份）：

```json
[
  "data/raw/HR/doc_20.txt",
  "data/raw/HR/doc_21.txt",
  "data/raw/HR/doc_22.txt",
  "data/raw/HR/doc_23.txt",
  "data/raw/HR/doc_24.txt"
]
```

狀態檔摘要：

```text
files tracked: 25
processed: 25
```

## 4. 驗收標準 DoD 對照

- [x] 準備 25 份假的 `pending_queue` 項目 → 驗證輸出剛好 20 份，且記錄下次要處理的清單。
- [x] 對「已處理過且未變更」的檔案 → 不會重複輸出。
- [x] 證據報告附上 25 份輸入 → 20 份輸出的實際執行結果。

## 5. 已知但本次未處理的項目

- 執行 `pytest tests/` 時，`tests/test_schemas.py::test_golden_qa_yaml_is_loadable` 會因 `tests/golden_qa.yaml` 現有 14 題（測試預期 3 題）而失敗。此問題屬於黃金測試集維護，不在本次 `doc_watcher.py` 任務範圍內，因此未修改該測試或 `golden_qa.yaml`。

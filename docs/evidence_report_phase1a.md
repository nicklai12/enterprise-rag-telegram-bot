# Phase 1a 證據報告 — doc_classifier.py

## 1. 本次實作內容

- 新增 `doc_classifier.py`：
  - 讀取 `config/pipeline.yaml` 的 `auto_process_rules`（`allow_paths` + `deny_keywords`）。
  - 掃描 `data/raw/**` 所有檔案。
  - 純規則式分類，不使用 LLM：
    - 檔名或路徑命中任一 `deny_keywords` → `needs_review`。
    - 檔案不在任一 `allow_paths` 底下 → `needs_review`。
    - 其餘 → `auto_process`。
  - 輸出 `pending_queue.json`。
- 新增 `tests/test_doc_classifier.py`：覆蓋三種情境並驗證 schema。

## 2. 實作假設與解讀（請你確認）

| 項目 | 本實作選擇 | 其他可能解讀 |
|---|---|---|
| 輸出欄位名稱 | 使用 `pending_queue.schema.json` 定義的 `decision` 欄位，值為 `auto_process` / `needs_review`。 | 提示詞寫 `classification = "auto" / "manual_review"`，但 schema 沒有 `classification` 欄位也沒有 `auto`/`manual_review` 列舉。 |
| 部門欄位 | 用 `pipeline.yaml` 的 `watch_folders` 路徑前綴比對來決定 `department`；配不到則 `"unknown"`。 | 也可以只從 `allow_paths` 推，但 `allow_paths` 不含部門資訊。 |
| doc_id | 由相對於 repo root 的檔案路徑去掉副檔名、把 `/` 換成 `_` 產生，確保唯一且穩定。 | 也可以用檔名 stem，但多層目錄或同名檔案會衝突。 |

## 3. 驗證方式

### 3.1 單元測試

```bash
pytest tests/test_doc_classifier.py -v
```

實際輸出：

```text
============================= test session starts ==============================
platform linux -- Python 3.12.1, pytest-7.4.4, pluggly-1.6.0 -- /usr/local/python/3.12.1/bin/python3
cachedir: .pytest_cache
rootdir: /workspaces/enterprise-rag-telegram-bot
plugins: anyio-4.12.1
collecting ... collected 4 items

tests/test_doc_classifier.py::test_deny_keyword_triggers_manual_review PASSED [ 25%]
tests/test_doc_classifier.py::test_file_outside_allow_paths_triggers_manual_review PASSED [ 50%]
tests/test_doc_classifier.py::test_clean_file_is_auto_processed PASSED   [ 75%]
tests/test_doc_classifier.py::test_output_validates_against_schema PASSED [100%]

============================== 4 passed in 1.55s ===============================
```

### 3.2 Schema 驗證

```bash
python doc_classifier.py
python -m jsonschema schemas/pending_queue.schema.json -i pending_queue.json
```

結果無錯誤，表示輸出通過 `pending_queue.schema.json`。

## 4. 三種情境實際輸出 JSON

使用以下臨時目錄結構：

```text
data/raw/HR/員工合約.pdf       # 命中 deny_keywords
data/raw/Finance/財報.xlsx     # 不在 allow_paths 白名單
data/raw/HR/請假規則.txt       # 乾淨檔案
```

執行 `doc_classifier.run(...)` 後的 `pending_queue.json`：

```json
[
  {
    "doc_id": "data_raw_Finance_財報",
    "source_file": "data/raw/Finance/財報.xlsx",
    "department": "unknown",
    "decision": "needs_review",
    "reason": "不在 allow_paths 白名單內"
  },
  {
    "doc_id": "data_raw_HR_員工合約",
    "source_file": "data/raw/HR/員工合約.pdf",
    "department": "HR",
    "decision": "needs_review",
    "reason": "命中黑名單關鍵字：合約"
  },
  {
    "doc_id": "data_raw_HR_請假規則",
    "source_file": "data/raw/HR/請假規則.txt",
    "department": "HR",
    "decision": "auto_process"
  }
]
```

## 5. 驗收標準 DoD 對照

- [x] 對一個命中 deny_keywords 的檔案（如檔名含「合約」）→ 輸出 `needs_review`。
- [x] 對一個不在 allow_paths 的檔案 → 輸出 `needs_review`。
- [x] 對一個乾淨檔案 → 輸出 `auto_process`。
- [x] 輸出檔通過 `schemas/pending_queue.schema.json` 驗證。
- [x] 證據報告附上三種情境各自的實際輸出 JSON。

## 6. 已知但本次未處理的項目

- 執行 `pytest tests/` 時，`tests/test_schemas.py::test_golden_qa_yaml_is_loadable` 會因 `tests/golden_qa.yaml` 現有 14 題（測試預期 3 題）而失敗。此問題屬於黃金測試集維護，不在本次 `doc_classifier.py` 任務範圍內，因此未修改該測試或 golden_qa.yaml。

# Phase 3b 證據報告 — verify_index.py

日期：2026-09-06 ｜ 範圍：spec.md §5.7（獨立審計，只讀）

## 做了什麼

- 新增 `verify_index.py`：對候選 `kb_run_id` 做三項稽核
  1. **數量一致**：加總 `data/chunks/*.manifest.json` 的 `chunk_count`（期望）
     vs. Chroma data collection `get(where={"kb_run_id": candidate})` 的實際筆數。
  2. **無重複 ID**：對 `get()` 回來的 ids 做 `total vs unique` 檢查，列出重複 id。
  3. **golden_qa 命中率 ≥ 80%**：以 `pipeline.yaml:embedding.model` embed 問題，
     `query(n_results=retrieval.golden_top_k, where={"kb_run_id": candidate})`，
     任一結果 metadata `doc_id == expected_doc_id` 算命中。
- 任一項不通過 → `main()` 回傳 exit code 1，並在 `status/kb_status.json` 的
  `runs[]` 附加一筆 `status: failed`、summary 含三項檢查的實際數字；
  **不更新 `active_kb_run_id`**（發布指標留給 publisher.py）。
- 新增 `tests/test_verify_index.py`：全部使用本地 ephemeral chromadb +
  deterministic keyword-bag fake embedder，無 Chroma Cloud / Telegram / Groq 請求。

## 假設（已在 PR 描述請確認）

1. 期望 chunk 數來源選 chunks **manifest** 的 `chunk_count` 加總（spec 允許 manifest 或 chunks json 二選一）。
2. Chroma 以 id 為唯一鍵，正常寫入不會產生重複 id；重複檢查是防禦性稽核，測試以 stub 造出重複情境。
3. 稽核結論寫入 `status/kb_status.json` 的 `runs[]`（符合 `schemas/status.schema.json`）。

## 怎麼驗證（實際輸出）

### 測試

```
$ python3 -m pytest tests/test_verify_index.py -v
tests/test_verify_index.py::test_count_mismatch_fails PASSED
tests/test_verify_index.py::test_duplicate_ids_fail PASSED
tests/test_verify_index.py::test_golden_hit_rate_66_percent_fails PASSED
tests/test_verify_index.py::test_all_checks_pass PASSED
4 passed in 1.12s
```

完整套件：`43 passed, 1 failed` —— 唯一失敗是 `tests/test_schemas.py::test_golden_qa_yaml_is_loadable`
（`assert len(qa) == 3`，但 golden_qa.yaml 已有 14 題真實題目）。此失敗在 main 上即存在
（本次僅新增 `verify_index.py` / `tests/test_verify_index.py`，未動該兩檔），
依「單一職責」未代為修改。

### 四種情境實際輸出（逐項檢查數字）

以下為 `verify_index.run()` 回傳報告的實際 JSON（透過臨時腳本對 ephemeral chromadb 執行）：

**情境 1：數量不一致（manifest=3，Chroma 實際 2）→ 判定失敗（exit 1）**
```json
"count_match": {"passed": false, "expected": 3, "actual": 2}
```
（其餘兩項 passed: true，整體 passed: false）

**情境 2：重複 ID（get 回 4 筆、唯一 3）→ 判定失敗（exit 1）**
```json
"count_match": {"passed": false, "expected": 3, "actual": 4},
"no_duplicate_ids": {"passed": false, "total": 4, "unique": 3,
  "duplicate_ids": ["20260906-120000_leave_rules_0"]}
```

**情境 3：golden 命中率 66%（2/3）→ 判定失敗（exit 1，< 0.8）**
```json
"count_match": {"passed": true, "expected": 3, "actual": 3},
"no_duplicate_ids": {"passed": true, "total": 3, "unique": 3, "duplicate_ids": []},
"golden_qa_hit_rate": {"passed": false, "total": 3, "hits": 2, "hit_rate": 0.6667,
  "threshold": 0.8, "top_k": 3,
  "details": [
    {"question": "特休有幾天？", "expected_doc_id": "leave_rules", "hit": true},
    {"question": "事假如何申請？", "expected_doc_id": "personal_leave", "hit": true},
    {"question": "差旅費如何報銷？", "expected_doc_id": "missing_doc", "hit": false}]}
```

**情境 4：全部通過（3/3 命中、數量一致、無重複）→ 判定通過（exit 0）**
```json
"count_match": {"passed": true, "expected": 3, "actual": 3},
"no_duplicate_ids": {"passed": true, "total": 3, "unique": 3, "duplicate_ids": []},
"golden_qa_hit_rate": {"passed": true, "total": 3, "hits": 3, "hit_rate": 1.0,
  "threshold": 0.8, "top_k": 3, "details": [ ... 三題 hit: true ... ]}
```

### status 寫入驗證

以 `jsonschema.validate` 對 `schemas/status.schema.json` 驗證 verify 寫出的
`kb_status.json` 通過；`runs[-1]` 實際內容：

```json
{"kb_run_id": "20260906-120000", "started_at": "2026-09-06T04:46:56.253671+00:00",
 "finished_at": "2026-09-06T04:46:56.259780+00:00", "status": "success",
 "summary": { "passed": true, "checks": { ... 三項檢查含實際數字 ... } }}
```

失敗情境的 status 為 `"status": "failed"` 且 summary 內含失敗項的 expected/actual（見測試
`test_count_mismatch_fails` 的斷言）；`active_kb_run_id` 恆未被改動（測試有斷言）。

## DoD 對照

- [x] 數量不一致情境 → 正確判定失敗（expected 3 / actual 2，exit 1）
- [x] 重複 id 情境 → 正確判定失敗（total 4 / unique 3，列出重複 id）
- [x] 3 題 golden、2 對 1 錯（hit_rate 0.6667 < 0.8）→ 正確判定失敗
- [x] 3 題全對（hit_rate 1.0）→ 正確判定通過（exit 0）
- [x] 四種情境實際輸出（含具體數字）已附於上方

## 備註（未動手處理）

- `tests/test_schemas.py::test_golden_qa_yaml_is_loadable` 在 main 分支已失敗
  （fixture 預期 3 題 vs. 現有 14 題），與本模組無關，依規則未代改。
- repo 的 `__pycache__/` 留有 `verify_index.cpython-312.pyc` 與
  `test_verify_index.cpython-312-pytest-7.4.4.pyc`，但 git 追蹤的檔案中並無對應來源，
  疑似過往工作痕跡；依規則（不刪 dead code / 不動未要求檔案）僅在此記錄。

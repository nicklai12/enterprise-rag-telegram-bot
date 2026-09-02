# Phase 2a 證據報告 — parser.py

## 1. 本次實作內容

- 新增 `parser.py`（spec §5.3）：
  - 以 CLI 單一文件模式執行：`python parser.py <source_file>`。
  - 用 `unstructured.partition.auto.partition` 解析 PDF / Word / Excel / txt。
  - 成功：寫出 `data/parsed/{doc_id}.json`（符合 `parsed_doc.schema.json`，元素含
    `type` / `text` / `metadata`）+ `data/parsed/{doc_id}.manifest.json`
    （`stage: "parser"`, `status: "success"`）。
  - 失敗（解析錯誤、檔案不存在、不支援格式）：manifest 寫 `status: "failed"` 並把錯誤
    訊息記入 `parse_errors`；**不 raise**，process 正常結束（exit code 0）。
  - `doc_id` / `department` 優先取自 `pending_queue.json`（classifier 的權威輸出），
    找不到時用 `doc_classifier` 的同一套規則推導，保證與上游一致。
  - element metadata 僅保留 JSON-serializable 的值，確保輸出可寫入 json。
- 新增 `tests/test_parser.py`：成功輸出 + schema 驗證、損毀檔案、檔案不存在、
  不支援格式、doc_id 解析（queue 優先）共 6 個測試。
- `requirements.txt` 僅新增一行：`unstructured[pdf,docx,xlsx]>=0.16,<0.19`
  （`[pdf,docx,xlsx]` extras 涵蓋 spec 指定的三種格式解析所需依賴）。

## 2. 實作假設與解讀（請你確認）

| 項目 | 本實作選擇 | 其他可能解讀 |
|---|---|---|
| manifest `status` 成功值 | `"success"`（DoD 原文明寫 `status: success`）。 | spec §6 未列舉 status 列舉值，`test_schemas.py` 範例用 `"parsed"`；若你想統一成 `"parsed"` 我可以改。 |
| manifest 檔名 | `data/parsed/{doc_id}.manifest.json`（依 system-map 目錄結構 `{doc_id}.json + .manifest.json`）。 | 也有人放 `data/manifests/`，但 system-map 明確寫在 parsed/ 下。 |
| CLI 介面 | 接收 repo-relative 的 `source_file` 路徑一個參數，stdout 印 manifest JSON，永遠 exit 0（參數錯誤才 exit 2）。 | 也可以改為吃 doc_id 或一次吃整批；但任務描述明確「被上層以單一文件呼叫」。 |
| `doc_id`/`department` 來源 | 先查 `pending_queue.json`，查不到才用 classifier 規則推導。 | 也可以只查 queue、查不到就報錯；但這會讓 parser 無法對 queue 以外的檔案獨立運作。 |
| PDF 解析依賴 | requirements 用 `unstructured[pdf,docx,xlsx]` 一行（CI 的 Ubuntu runner 另需系統層 `poppler-utils` 與 `libgl1`，這是 unstructured PDF 路徑的已知需求，屬 workflow 階段處理）。 | 也可以用 `unstructured[all-docs]`，但體積大很多且含用不到的 detectron 等。 |

## 3. 驗證方式

### 3.1 單元測試

```bash
pytest tests/test_parser.py -v
```

實際輸出：

```text
tests/test_parser.py::test_successful_parse_writes_parsed_json_and_manifest PASSED [ 16%]
tests/test_parser.py::test_outputs_validate_against_schemas PASSED       [ 33%]
tests/test_parser.py::test_corrupted_file_records_failed_manifest_without_raising PASSED [ 50%]
tests/test_parser.py::test_missing_file_records_failed_manifest PASSED   [ 66%]
tests/test_parser.py::test_unsupported_format_records_failed_manifest PASSED [ 83%]
tests/test_parser.py::test_resolve_doc_info_uses_pending_queue_then_classifier PASSED [100%]
============================== 6 passed in 7.80s ===============================
```

完整 `pytest tests/`：29 passed, 1 failed —— 唯一失敗是
`test_schemas.py::test_golden_qa_yaml_is_loadable`（golden_qa.yaml 現有 14 題但測試寫死 3 題），
此問題 Phase 1b 報告已記載，屬黃金測試集維護，不在本次範圍。

### 3.2 成功情境 — 真實 PDF（repo 內 `data/raw/HR/1930年代國民政府的造林事業.PDF`）

```bash
python3 parser.py "data/raw/HR/1930年代國民政府的造林事業.PDF"; echo "exit_code=$?"
```

實際輸出：

```json
{
  "doc_id": "data_raw_HR_1930年代國民政府的造林事業",
  "source_file": "data/raw/HR/1930年代國民政府的造林事業.PDF",
  "department": "HR",
  "status": "success",
  "timestamp": "2026-09-02T14:46:44.075886+00:00",
  "parse_errors": [],
  "stage": "parser"
}
exit_code=0
```

產出的 parsed json 抽樣（共 11,549 個 elements）：

```text
elements: 11549
Title | ‧國立政治大學‧National Chengchi University
  metadata keys: ['coordinates', 'file_directory', 'filename', 'filetype', 'languages', 'last_modified', 'page_number']
Title | 國立政治大學歷史學系
Title | 碩士學位論文
```

schema 驗證（實際執行）：

```text
data_raw_HR_1930年代國民政府的造林事業 -> manifest schema OK, status = success
real-PDF parsed json schema OK, elements = 11549
```

### 3.3 失敗情境 — 損毀 PDF（暫存於 data/raw/HR，驗證後已刪除）

```bash
printf '%%PDF-1.4 this is not a real pdf' > "data/raw/HR/zz_evidence_broken.pdf"
python3 parser.py "data/raw/HR/zz_evidence_broken.pdf"; echo "exit_code=$?"
```

實際輸出：

```json
{
  "doc_id": "data_raw_HR_zz_evidence_broken",
  "source_file": "data/raw/HR/zz_evidence_broken.pdf",
  "department": "HR",
  "status": "failed",
  "timestamp": "2026-09-02T14:47:40.616044+00:00",
  "parse_errors": [
    "Unable to get page count.\nSyntax Error: Couldn't find trailer dictionary\nSyntax Error: Couldn't find trailer dictionary\nSyntax Error: Couldn't read xref table\n"
  ],
  "stage": "parser"
}
exit_code=0
```

- manifest 通過 `manifest.schema.json` 驗證（見 3.2 驗證輸出第一行）。
- 失敗時**沒有**產出 `data/parsed/{doc_id}.json`（實際檢查：`broken parsed json exists: False`）。

## 4. 驗收標準 DoD 對照

- [x] 用一份正常的假 PDF/txt → 產出 parsed json + manifest(status: success)
      （單元測試用假 txt；另以 repo 內真實 PDF 做 CLI 端到端驗證，兩者皆通過 schema 驗證）
- [x] 用一份損毀/不支援格式的檔案 → manifest 寫 status: failed，parser.py exit code 0，
      未拋出未捕捉例外（單元測試 3 種失敗情境 + CLI 實跑 exit_code=0）
- [x] 輸出通過對應 schema 驗證（parsed_doc.schema.json 與 manifest.schema.json 皆以
      jsonschema 實際驗證成功）
- [x] 證據報告附上成功與失敗兩種情境的實際 manifest 內容（見 3.2 / 3.3）

## 5. 已知但本次未處理的項目

- `pytest tests/` 中 `test_golden_qa_yaml_is_loadable` 的既有失敗（golden_qa.yaml 題數與測試
  預期不符），Phase 1b 已記載，不屬本任務範圍。
- 本環境安裝 unstructured PDF 路徑需要系統套件 `poppler-utils` 與 `libgl1`；GitHub Actions
  的 workflow（Phase 5）需安裝這兩個系統套件，屆時在 workflow 階段處理。

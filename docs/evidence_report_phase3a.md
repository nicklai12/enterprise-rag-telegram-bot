# 證據報告 — Phase 3a: indexer.py

## 做了什麼

依 spec.md §5.6、§4 實作 `indexer.py`，沿用 embedder.py 的結構風格：

1. **產生 kb_run_id**：格式 `YYYYMMDD-HHMMSS`（spec §4.1 建議格式），
   CLI 每次執行自動產生；`run()` 接受 `kb_run_id` 參數供測試/重跑注入。
2. **連線 Chroma**：`chromadb.CloudClient(api_key=..., tenant=..., database=...)`
   ，三個憑證一律讀環境變數 `CHROMA_API_KEY` / `CHROMA_TENANT` /
   `CHROMA_DATABASE`（spec §7 註記），collection 名稱取自
   `pipeline.yaml:vectorstore.data_collection_name`（預設 `company_kb_data`）。
3. **寫入 data collection**（檔案交接：讀 chunks JSON + embeddings .npy）：
   - ids：`{kb_run_id}_{doc_id}_{chunk_index}`（spec §4.3，版本共存不保證覆蓋）
   - metadatas：`kb_run_id` / `doc_id` / `chunk_index` / `source_file` /
     `department`（spec §4.1 五個必填欄位）
   - documents：chunk text；embeddings：.npy 向量（`vectors.tolist()`）
   - 用 `upsert` 寫入（同 kb_run_id 重跑冪等）
4. **失敗行為**：連線或寫入任何例外 → stderr 印 `indexer failed: ...` →
   `sys.exit(1)`（整批中止，spec §5.6）。
5. 輸出 manifest 型結果（`stage="indexer"`、`status="indexed"`、
   `kb_run_id` / `collection_name` / `upsert_count`），可通過
   `schemas/manifest.schema.json` 驗證（schema 已含 indexer 欄位與列舉值）。
6. 新增 `tests/test_indexer.py`（6 個測試，全部只用
   `chromadb.EphemeralClient`，未對正式 Chroma Cloud 發出任何請求），
   requirements.txt 新增一行 `chromadb>=0.5,<1.0`（本機實際安裝 0.6.3）。

## 假設與說明（請確認）

- **單次處理單一文件**：與 parser/chunker/embedder 各階段相同粒度，一次
  `run()` 處理一份 chunks json + 對應 npy（spec 未要求 indexer 內部批次彙總
  多份文件；批次編排屬 GitHub Actions workflow 職責）。
- **manifest `status` 值**：spec §6 未規定 indexer 階段 status 字串，採用
  `"indexed"`（與 chunker `"chunked"` / embedder `"embedded"` 命名模式一致）。
- **`upsert` vs `add`**：spec 只說「寫入」，選 `upsert` 使同 kb_run_id 重跑
  冪等，不影響「不同 kb_run_id 共存」語意。
- **.npy 列數與 chunk 數不符**：視為資料交接錯誤直接 raise（exit 1），
  不靜默寫入。
- chromadb 會嘗試送 telemetry 到 PostHog，本機出現
  `Failed to send telemetry event ... capture() takes 1 positional argument`
  警告，屬 chromadb/posthog 套件版本相容性問題，不影響功能與測試結果。

## 怎麼驗證（實際輸出）

### 1. pytest（本模組 6 項全過）

```
tests/test_indexer.py::test_ephemeral_write_then_query_by_kb_run_id PASSED
tests/test_indexer.py::test_two_kb_run_ids_for_same_doc_coexist PASSED
tests/test_indexer.py::test_connection_failure_exits_nonzero PASSED
tests/test_indexer.py::test_missing_env_credentials_exits_nonzero PASSED
tests/test_indexer.py::test_write_failure_exits_nonzero PASSED
tests/test_indexer.py::test_new_kb_run_id_format PASSED
6 passed in 2.86s
```

涵蓋：DoD-1（ephemeral 寫入後以 `where kb_run_id` 查回、數量/ids/metadata/
documents/embeddings 逐項斷言 + manifest 過 schema）、DoD-3（兩個 kb_run_id
共存）、DoD-2（連線失敗 / 缺環境變數 / 寫入失敗三種 exit 非 0）、kb_run_id
格式（`20260904-133045`）。

### 2. DoD-1 實跑：ephemeral 寫入 dev fixture → 查得回

指令：`python` 內嵌腳本，以 `chromadb.EphemeralClient()` 注入
`indexer.run(..., kb_run_id="20260904-120000")`。

```
DoD-1 manifest: {'kb_run_id': '20260904-120000', 'upsert_count': 1, 'collection_name': 'company_kb_data', 'status': 'indexed'}
DoD-1 queried ids: ['20260904-120000_sample_0']
DoD-1 count == input chunks: True
DoD-1 metadata: {'chunk_index': 0, 'department': 'HR', 'doc_id': 'sample', 'kb_run_id': '20260904-120000', 'source_file': 'data/raw/HR/sample_raw.txt'}
DoD-1 document: 人力資源部請假規則
一、特休。員工每滿六個月可享三日特休，滿 ...
```

dev fixture 1 個 chunk → 查回 1 筆，id/metadata/document 皆正確。

### 3. DoD-3 實跑：同 doc_id 兩個 kb_run_id 共存

同上腳本第二次 `run(kb_run_id="20260904-130000")`：

```
DoD-3 total count after 2 runs: 2
DoD-3 run A count: ['20260904-120000_sample_0']
DoD-3 run B count: ['20260904-130000_sample_0']
```

兩版本各 1 筆、id 前綴不同、互不覆蓋。

### 4. DoD-2 實跑：連線失敗 → exit code 非 0

指令：`env -u CHROMA_API_KEY -u CHROMA_TENANT -u CHROMA_DATABASE python indexer.py`

```
indexer failed: 'CHROMA_API_KEY'
exit code: 1
```

（缺憑證在建立 CloudClient 之前即 raise，未發出任何網路請求；寫入失敗的
exit 非 0 情境由 mock client 的 pytest 覆蓋，見 test_write_failure_exits_nonzero。）

### 5. 全量測試

`python -m pytest tests/ -q` → `1 failed, 39 passed`。失敗的
`tests/test_schemas.py::test_golden_qa_yaml_is_loadable` 與 phase 2c 報告相同，
在乾淨 main 上即存在（既有問題），依規則不動手修改，僅回報。

## 驗收標準 DoD 逐項

- [x] 用本地 ephemeral chromadb 寫入 fixture 資料 → client.get() 查得到剛寫入
      的 kb_run_id 資料，數量與輸入 chunk 數一致：
      `test_ephemeral_write_then_query_by_kb_run_id`（3 chunks → `len(ids)==3`，
      另以 dev fixture 實跑 1 chunk → 查回 1 筆，見上方實際輸出）
- [x] 故意讓連線失敗（給錯設定）→ exit code 非 0：缺環境變數實跑
      `exit code: 1`；mock 連線/寫入失敗 pytest 斷言 `SystemExit.code != 0`
- [x] 同一份 doc_id 用兩個不同 kb_run_id 各寫一次 → 兩份資料共存：
      `collection.count()==2`，兩個 where 查詢各回 1 筆且 id 不同
- [x] 證據報告附上三種情境的實際指令與輸出/exit code：見上方 §2–§4

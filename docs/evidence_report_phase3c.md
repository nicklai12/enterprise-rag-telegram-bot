# Phase 3c 證據報告 — publisher.py

- 日期：2026-09-06
- Branch：`phase3c-publisher`
- PR：#10
- 依據：spec.md §5.8（發布）、§4.2（active 指標）、§7（pipeline.yaml 控制面）

## 做了什麼

新增 `publisher.py`（無修改任何既有模組）：

1. 假設呼叫時 verify 已通過（workflow 層保證順序），本程式不重跑審計。
2. 以 `chromadb.CloudClient`（憑證由 `CHROMA_API_KEY / CHROMA_TENANT / CHROMA_DATABASE` 環境變數注入）連線，對 control collection（`pipeline.yaml:vectorstore.control_collection_name`，預設 `company_kb_control`）的固定 id（`control_pointer_id`，預設 `active_pointer`）執行單筆 upsert：
   - metadata：`{ "active_kb_run_id": "<candidate>", "published_at": "<ISO-8601 UTC>" }`
   - documents： `"active pointer -> kb_run_id <candidate>"`（此 chromadb 版本要求 upsert 必須附 documents/images，故補上；spec 只規範 metadata 內容）
3. 任何連線/寫入失敗 → 印出錯誤至 stderr，`sys.exit(1)`；因為是單筆原子 upsert，失敗時舊指標原封不動，無部分寫入髒資料。
4. `run()` 比照 indexer.py 慣例可注入 `client`，供測試使用本地 ephemeral chromadb。

測試：`tests/test_publisher.py`（全部使用本地 ephemeral chromadb，無 Chroma Cloud / Telegram / Groq 真實請求）：

- `test_publish_updates_active_pointer`：發布前後以 client 讀回 `active_pointer`，斷言 `active_kb_run_id` 確實更新為候選版本。
- `test_publish_creates_pointer_when_absent`：首次發布時建立固定 id 紀錄。
- `test_failed_write_keeps_previous_pointer_and_exits_nonzero`：模擬 upsert 拋例外 → `main()` exit code 1，且 control collection 內舊指標完全未變。
- `test_run_manifest_is_json_serializable`：`run()` 回傳 manifest 可安全印出。

## 假設（請確認）

1. **publisher 不寫 `status/kb_status.json`**：spec §5.8 只規定「寫 control collection + 失敗 exit 非 0」，未要求更新 status 檔；且 indexer.py（phase 3a 已核准）也不寫 status，status 由 verify_index.py 統一管理。system-map.md 表格雖把 status 列為 publisher 輸出，但依「spec 為唯一依據」原則未實作。若你要 publisher 也記一筆 status，請告知，我補上。
2. `published_at` 採 ISO-8601 UTC（與 indexer/verify 的 timestamp 慣例一致）。

## 怎麼驗證（實際輸出）

### 1. 自動化測試

```
$ python -m pytest tests/test_publisher.py -v
tests/test_publisher.py::test_publish_updates_active_pointer PASSED
tests/test_publisher.py::test_publish_creates_pointer_when_absent PASSED
tests/test_publisher.py::test_failed_write_keeps_previous_pointer_and_exits_nonzero PASSED
tests/test_publisher.py::test_run_manifest_is_json_serializable PASSED
4 passed
```

全量測試：`47 passed, 1 failed` — 唯一失敗 `tests/test_schemas.py::test_golden_qa_yaml_is_loadable` 為**既有問題**（golden_qa.yaml 現有 14 題，該測試斷言 3 題），與本相位無關，未動手修改（依硬性規則 5）。

### 2. 發布前後 control collection 內容對比（本地 ephemeral chromadb）

**發布前**（`client.get(ids=["active_pointer"])` 實際輸出）：

```json
{
  "ids": ["active_pointer"],
  "metadatas": [
    {
      "active_kb_run_id": "20260905-090000",
      "published_at": "2026-09-05T09:00:00+00:00"
    }
  ]
}
```

執行 `publisher.run(kb_run_id="20260906-130000", client=...)`，回傳 manifest：

```json
{
  "status": "published",
  "timestamp": "2026-09-06T05:02:25.806119+00:00",
  "kb_run_id": "20260906-130000",
  "collection_name": "company_kb_control",
  "pointer_id": "active_pointer",
  "active_kb_run_id": "20260906-130000",
  "published_at": "2026-09-06T05:02:25.806119+00:00"
}
```

**發布後**（同一筆紀錄讀回）：

```json
{
  "ids": ["active_pointer"],
  "documents": ["active pointer -> kb_run_id 20260906-130000"],
  "metadatas": [
    {
      "active_kb_run_id": "20260906-130000",
      "published_at": "2026-09-06T05:02:25.806119+00:00"
    }
  ]
}
```

### 3. 故意讓寫入失敗

模擬 control collection upsert 拋 `RuntimeError("simulated write failure")`，以 CLI 路徑呼叫 `publisher.main()`：

```
publisher failed: simulated write failure
=== FAILED PUBLISH: exit code = 1 ===
pointer after failed publish: [{"active_kb_run_id": "20260906-130000", "published_at": "2026-09-06T05:02:25.806119+00:00"}]
```

→ exit code 非 0，且舊指標（`20260906-130000`）原封不動，無殘留髒資料。

## DoD 對照

- [x] 執行後，用 client 讀 control collection 的 active_pointer → active_kb_run_id 確實更新成候選版本（見上方發布前後對比：20260905-090000 → 20260906-130000）
- [x] 故意讓寫入失敗 → exit code 非 0（=1），且沒有殘留部分寫入的髒資料（失敗後指標維持舊值）
- [x] 證據報告附上發布前後 control collection 內容對比

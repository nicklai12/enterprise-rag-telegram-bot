# Spec — 企業 RAG 知識庫 + Telegram Bot（MVP, 繁中為主, Chroma Cloud, 做法 B 發布）

## 0. 已確認的前提（你提供的決策）
1. **以繁體中文為主**（文件與問答預期多為繁中）。
2. 目前僅測試用途：**Bot 不限制使用者存取**（但保留 allowlist 作為可選護欄，預設關閉）。
3. 向量庫採 **Chroma Cloud Starter（免費）**。
4. 採用 **做法 B：kb_run_id 版本標籤 + active 指標**，確保「審核不過＝不發布」。

---

## 1. 目標
- 文件從進入 `data/raw/` 到能被 Bot 正確引用回答，整條路徑可追蹤、可驗收、可中止。
- Pipeline 每次跑出一個「候選版本」，只有通過審計才發布成 Bot 可查版本。

---

## 2. 範圍

### In Scope（MVP）
- PDF / Word / Excel 解析（Unstructured）
- hierarchical chunking
- embedding（以中文為主的模型）
- Chroma Cloud：data/control 兩個 collections
- GitHub Actions 排程更新（每日一次）
- Telegram Bot + Groq 回答 + 引用來源
- 黃金測試集（20 題，繁中為主）+ 命中率門檻
- 做法 B 發布：`kb_run_id` + `active_kb_run_id`

### Out of Scope（本階段不做）
- 部門級/多租戶權限隔離
- OCR 掃描件
- 自動用量監控（Chroma/Groq 配額）
- 內容敏感度 AI 分類（僅規則式）
- 自動清理舊 kb_run_id 版本（僅列為已知限制）

---

## 3. 技術棧
- Parsing：Unstructured.io
- Chunking / Retrieval：LlamaIndex（可搭 LangChain 組裝 prompt，但不要求一定用）
- Embedding：Sentence-Transformers（**中文為主模型**，預設 `BAAI/bge-small-zh-v1.5`；可在 pipeline.yaml 調整）
- Vector DB：Chroma Cloud（CloudClient）
- LLM：Groq
- Bot：python-telegram-bot（polling）
- CI/CD：GitHub Actions（concurrency lock）
- Hosting：Render free tier

---

## 4. 資料模型與版本發布（做法 B）

### 4.1 kb_run_id
- 每次 pipeline 執行會產生一個 `kb_run_id`（建議格式：`YYYYMMDD-HHMMSS`）。
- `indexer.py` 寫入 Chroma Cloud data collection 時，每個 chunk 的 metadata **必須包含**：
  - `kb_run_id`
  - `doc_id`
  - `chunk_index`
  - `source_file`
  - `department`

### 4.2 Active 指標（control collection）
- 使用單獨的 control collection（例如 `company_kb_control`）保存目前「已發布」版本：
  - 以固定 id（例如 `active_pointer`）存一筆紀錄
  - metadata 內含 `active_kb_run_id`
- Bot 啟動/每次查詢前，會讀取 `active_kb_run_id`，並只檢索該版本（where filter）。

### 4.3 ID 規則（避免不同版本互相覆蓋）
- data collection 的 chunk 唯一 id 建議為：
  - `{kb_run_id}_{doc_id}_{chunk_index}`
- 目的：同一份文件不同版本可以共存，發布用 active 指標切換。

---

## 5. 腳本規格（逐元件）

> 共通要求：各階段以檔案交接（json/npy/manifest），不得用記憶體物件偷偷串接。

### 5.1 doc_classifier.py
- 依 `pipeline.yaml:auto_process_rules` 規則輸出 `pending_queue.json`
- 規則式（資料夾白名單 + 關鍵字黑名單），**不使用 LLM 判斷敏感度**

### 5.2 doc_watcher.py
- 找出新增/變更文件
- 防禦性設計：單次最多處理 20 份（超過留到下次）

### 5.3 parser.py
- 解析單一文件 → `data/parsed/{doc_id}.json` + manifest
- 單一文件失敗：寫 manifest `status: failed`，不中斷整批

### 5.4 chunker.py
- hierarchical chunking
- chunk 參數由 `pipeline.yaml:chunking` 控制
- 輸出 `data/chunks/{doc_id}.json`（含 chunk text + metadata）+ manifest

### 5.5 embedder.py
- 讀 chunks → 產生 embeddings → `data/embeddings/{doc_id}.npy` + manifest
- 模型由 `pipeline.yaml:embedding.model` 控制
- **向量維度不可在 spec 寫死**：以模型輸出為準，但需在 manifest 記錄 `embedding_dim`

### 5.6 indexer.py（寫入候選版本）
- 產生 `kb_run_id`
- 連線 Chroma Cloud（CloudClient，憑證由環境變數注入）
- 寫入 data collection（例如 `company_kb_data`）：
  - ids：`{kb_run_id}_{doc_id}_{chunk_index}`
  - metadatas：至少包含 `kb_run_id/doc_id/chunk_index/source_file/department`
  - documents：chunk text（供檢索引用）
  - embeddings：向量
- 失敗行為：連線/寫入失敗 → exit code 非 0（整批中止）

### 5.7 verify_index.py（獨立審計，只讀）
驗收對象是「候選 kb_run_id」，必須全部通過才算成功：
1. **數量一致（以 kb_run_id 篩選）**  
   - 期望 chunk 數：加總本次處理文件的 chunks（以 chunks manifest 或 chunks json 計算）
   - 實際 chunk 數：Chroma data collection where `kb_run_id == candidate`
2. **無重複 ID（在 kb_run_id 範圍內）**
3. **黃金測試集命中率 ≥ 80%**  
   - 對 `tests/golden_qa.yaml` 逐題檢索 top_n（由 pipeline.yaml 控制）
   - 檢查結果的 metadata 是否包含 `expected_doc_id`
- verify 失敗：exit code 非 0，**不得**更新 active 指標

### 5.8 publisher.py（發布）
- 前置條件：verify 通過（workflow 只有 verify 成功才會執行 publisher）
- 寫入 control collection 固定 id（例如 `active_pointer`）：
  - metadata: `{ "active_kb_run_id": "<candidate_kb_run_id>", "published_at": "..." }`
- publisher 失敗：exit code 非 0（本次不發布）

### 5.9 bot.py（只讀）
流程：
1. 連線 Chroma control collection 讀取 `active_kb_run_id`
2. 檢索 Chroma data collection（where: `kb_run_id == active_kb_run_id`）
3. 組 prompt → 呼叫 Groq → 回覆（含來源：source_file/doc_id 等）

Bot 存取控制（最小護欄）：
- **測試模式預設不限制**：若未設定 `BOT_ALLOWLIST`，則允許所有使用者/聊天室。
- 若設定 `BOT_ALLOWLIST`（例如逗號分隔 id），則只允許名單內的 chat_id 或 user_id。

---

## 6. Manifest Schema（結構化通訊）
沿用既有欄位，並建議各階段可加 stage-specific 欄位（不強制）：

必填（至少）：
- `doc_id`, `source_file`, `department`, `status`, `timestamp`
- `parse_errors`（可空陣列；非 parser 階段可維持空）

建議新增（選填但推薦）：
- `stage`（parser/chunker/embedder/indexer）
- `chunk_count`（chunker）
- `embedding_model`, `embedding_dim`, `vector_count`（embedder）
- `kb_run_id`, `collection_name`, `upsert_count`（indexer）

---

## 7. pipeline.yaml（控制面，不含密鑰）
至少包含：

```yaml
watch_folders:
    - path: "data/raw/HR"
    department: "HR"
    access_level: "internal"

auto_process_rules:
    allow_paths: ["data/raw/HR"]
    deny_keywords: ["合約", "薪資"]

chunking:
    strategy: "hierarchical"
    chunk_size: 512
    overlap: 0.2

embedding:
    model: "BAAI/bge-small-zh-v1.5"
    batch_size: 16

vectorstore:
    provider: "chroma_cloud"
    data_collection_name: "company_kb_data"
    control_collection_name: "company_kb_control"
    control_pointer_id: "active_pointer"

llm:
    provider: "groq"
    model: "llama-3.1-8b-instant"
    daily_request_cap: 500

retrieval:
    top_k: 5
    golden_top_k: 3
```

> 注意：`CHROMA_API_KEY / CHROMA_TENANT / CHROMA_DATABASE` 一律用環境變數注入。

---

## 8. GitHub Actions SOP（步驟）
建議步驟（同一 workflow 內依序執行，失敗即中止）：
1. classifier
2. watcher
3. parser
4. chunker
5. embedder
6. indexer（寫入候選 kb_run_id）
7. verify（只讀審計候選 kb_run_id）
8. publisher（更新 active 指標 = 發布）
9. 更新/commit `status/kb_status.json`（看板/報告）

---

## 9. 驗收標準（Definition of Done）
- [ ] 任一文件解析失敗不會讓整批 pipeline crash（parser 規則）
- [ ] indexer 寫入後，verify 能針對「候選 kb_run_id」完成三項稽核
- [ ] verify 失敗時，publisher 不會執行，Bot 不會切到新版本
- [ ] verify 通過後，publisher 更新 active_kb_run_id，Bot 查詢只命中該版本
- [ ] golden_qa 命中率 ≥ 80%

---

## 10. 已知限制（本階段承認但不解）
- Chroma Cloud 免費額度用盡需人工處理（未做自動監控/攔截）
- 做法 B 會累積舊 kb_run_id 資料：未提供自動清理舊版本機制
- Render free tier 可能 idle sleep 造成冷啟動延遲

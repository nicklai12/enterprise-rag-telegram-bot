# 企業 RAG 知識庫 + Telegram Bot（MVP｜Chroma Cloud｜做法 B：版本發布）

以**繁體中文文件/問答**為主，把企業內部文件（PDF/Word/Excel）透過排程流水線  
**解析 → 切塊 → 向量化 → 寫入 Chroma Cloud（候選版本） → 審計 → 發布**，並用 **Telegram Bot** 提供**可引用來源**的檢索式問答（RAG）。

本專案採用「**做法 B**」：每次 pipeline 產生 `kb_run_id` 當作版本標記；只有通過 `verify_index.py` 審計的候選版本，才會由 `publisher.py` 更新 control collection 的 `active_kb_run_id`，Bot 永遠只查「已發布版本」。

---

## 一句話定位

**可追蹤、可驗收、可中止**的企業內部知識庫更新流程：  
> 審核不過＝不發布（Bot 不會切到新資料）。

---

## 系統架構（MVP）

```text
config/pipeline.yaml  ───────────────────────────── 控制面（不含任何密鑰）
        │
GitHub Actions（排程觸發 + concurrency lock）
        │
doc_classifier.py  ──（Manager）判斷 🟢可自動 / 🔴需人工
        │ 只傳遞 🟢
doc_watcher.py → parser.py → chunker.py → embedder.py → indexer.py
        │            │          │            │            │
        │            └─ 每步輸出檔案 + manifest（結構化交接）
        │
        │ indexer.py 寫入「候選版本」到 Chroma Cloud（data collection）
        │  - 每次 pipeline 產生 kb_run_id
        │  - chunk metadata 會帶 kb_run_id
        ▼
┌──────────────────────────────────────────────────────────────┐
│                         Chroma Cloud                          │
│  Collection A: company_kb_data     （資料：chunks + embeddings）│
│  Collection B: company_kb_control  （控制：active_kb_run_id 指標）│
└───────────────┬──────────────────────────────────────────────┘
                │
verify_index.py │（獨立審計，只讀）：檢查「候選 kb_run_id」是否可用
                │
publisher.py    │（發布）：審計通過才更新 control collection 的 active 指標
                │
status/kb_status.json（看板/報告，commit 回 repo）
                │
Telegram Bot（Render 常駐，只讀）
    1) 讀 control collection 取得 active_kb_run_id
    2) 查 data collection（where filter: kb_run_id=active）
    3) Groq LLM 生成回答 + 引用來源
```

---

## MVP 範圍

### In Scope
- PDF / Word / Excel 解析（Unstructured）
- hierarchical chunking
- embedding（中文為主模型，預設 `BAAI/bge-small-zh-v1.5`）
- Chroma Cloud：`data/control` 兩個 collections
- GitHub Actions 每日排程更新（concurrency lock）
- Telegram Bot（python-telegram-bot polling）+ Groq 回答 + 引用來源
- 黃金測試集 `tests/golden_qa.yaml`（20 題、繁中為主）+ 命中率門檻
- 做法 B 發布：`kb_run_id` + `active_kb_run_id`

### Out of Scope（本階段不做）
- 部門級/多租戶權限隔離
- OCR 掃描件
- Chroma/Groq 用量監控（配額耗盡需人工處理）
- AI 敏感度分類（僅規則式）
- 自動清理舊 `kb_run_id`（已知限制）

---

## 版本與發布機制（做法 B）

### 1) `kb_run_id`（候選版本標記）
- 每次 pipeline 執行會產生一個 `kb_run_id`（建議格式：`YYYYMMDD-HHMMSS`）
- `indexer.py` 寫入 Chroma data collection 時，每個 chunk 的 metadata **至少包含**：
  - `kb_run_id`, `doc_id`, `chunk_index`, `source_file`, `department`

### 2) Active 指標（control collection）
- control collection（例如 `company_kb_control`）保存「目前已發布版本」
- 固定 id（例如 `active_pointer`）存一筆紀錄：
  - metadata 內含 `active_kb_run_id`
- Bot 每次查詢前讀取 `active_kb_run_id`，並只檢索該版本（where filter）

### 3) Chunk ID 規則（避免覆蓋）
- data collection 的 chunk 唯一 id 建議為：
  - `{kb_run_id}_{doc_id}_{chunk_index}`

---

## 目錄結構

```text
data/raw/{department}/                 # 原始文件（唯讀）
data/parsed/{doc_id}.json + .manifest.json
data/chunks/{doc_id}.json + .manifest.json
data/embeddings/{doc_id}.npy + .manifest.json

status/kb_status.json                  # 看板狀態（commit 回 repo）
tests/golden_qa.yaml                   # 黃金測試集（繁中為主）
config/pipeline.yaml                   # 控制面設定（不含密鑰）
schemas/*.schema.json                  # 交接契約（manifest/status）
.github/workflows/kb-pipeline.yml      # SOP（排程流水線）
```

---

## 外部服務與執行環境

| 元件 | 執行位置 | 常駐 | 備註 |
|------|----------|------|------|
| Pipeline（classifier→publisher） | GitHub Actions | 否 | 排程批次、concurrency lock |
| Bot | Render free tier | 是 | 可能 idle sleep |
| 向量庫 | Chroma Cloud（Starter） | 是 | data/control 兩個 collections |
| LLM | Groq | 是 | 僅 Bot 端呼叫 |

---

## 密鑰與環境變數（不進 repo）

| 變數 | 用途 | 放哪裡 |
|------|------|--------|
| `CHROMA_API_KEY` `CHROMA_TENANT` `CHROMA_DATABASE` | Actions 寫入/審計 + Bot 查詢 | GitHub Secrets + Render env |
| `GROQ_API_KEY` | Bot 生成回答 | Render env |
| `TELEGRAM_BOT_TOKEN` | Bot | Render env |
| `BOT_ALLOWLIST`（可選） | 最小安全護欄 | Render env（預設不設＝不限制） |
| `GROQ_MODEL`（可選） | 覆蓋 pipeline.yaml 的 `llm.model`，便於不改 repo 換 Groq 模型 | Render env（預設不設＝用 pipeline.yaml） |

> 注意：`config/pipeline.yaml` 為**控制面**，不得放任何密鑰。

---

## pipeline.yaml（控制面設定）

最少需包含（範例）：

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

---

## Pipeline（GitHub Actions SOP）

同一個 workflow 內依序執行，**任一步失敗即中止**：

1. `doc_classifier.py`
2. `doc_watcher.py`（單次最多處理 20 份）
3. `parser.py`（單一文件失敗不會讓整批 crash；寫 manifest `status: failed`）
4. `chunker.py`
5. `embedder.py`（manifest 需記錄 `embedding_dim`，不可在 spec 寫死）
6. `indexer.py`（寫入 Chroma：候選 `kb_run_id`；寫入失敗需 exit code ≠ 0）
7. `verify_index.py`（只讀審計候選 `kb_run_id`）
8. `publisher.py`（只有 verify 成功才執行：更新 `active_kb_run_id`）
9. 更新/commit `status/kb_status.json`

---

## 部署說明（GitHub Actions + Render）

### 1. GitHub Secrets（pipeline 用）

到 **Settings → Secrets and variables → Actions → New repository secret** 設定以下三個變數（只有變數名稱，值請自行填入，**不要** commit 進 repo）：

| Secret 變數名稱 | 用途 |
|---|---|
| `CHROMA_API_KEY` | kb-pipeline workflow 連線 Chroma Cloud（indexer 寫入 / verify 審計 / publisher 發布） |
| `CHROMA_TENANT` | Chroma Cloud tenant id |
| `CHROMA_DATABASE` | Chroma Cloud database name |

> pipeline（classifier→publisher）只需要這三個；`GROQ_API_KEY` / `TELEGRAM_BOT_TOKEN` 只在 Bot 端使用，不需要設為 GitHub Secret。

設定完成後，workflow 會依每日排程（UTC 23:17）自動執行；也可在 **Actions → kb-pipeline → Run workflow** 手動觸發（`workflow_dispatch`）。

### 2. Render 部署 bot.py（常駐）

1. Render 建立 **New → Web Service**，連接本 repo。
2. Runtime 選 **Python 3**；Build Command 留預設（`pip install -r requirements.txt`），Start Command 設為：
   ```
   python bot.py
   ```
3. 在 Web Service 的 **Environment** 加入以下環境變數（值請自行填入）：

| 環境變數名稱 | 用途 | 是否必填 |
|---|---|---|
| `CHROMA_API_KEY` | 讀取 control collection（active 指標）+ 檢索 data collection | 必填 |
| `CHROMA_TENANT` | Chroma Cloud tenant id | 必填 |
| `CHROMA_DATABASE` | Chroma Cloud database name | 必填 |
| `GROQ_API_KEY` | Groq LLM 生成回答 | 必填 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token（polling 模式） | 必填 |
| `BOT_ALLOWLIST` | 允許的 chat_id / user_id（逗號分隔）；**不設＝不限制**（測試模式預設） | 選填 |
| `GROQ_MODEL` | Groq 模型名稱，覆蓋 `config/pipeline.yaml` 的 `llm.model`（例如 `llama-3.3-70b-versatile`）；**不設＝使用 pipeline.yaml 預設值** | 選填 |

4. 方案選 Free 即可（注意：free tier 可能 idle sleep 造成冷啟動延遲，為已知限制）。
5. 部署後以真人 Telegram 帳號私訊 bot 一題知識庫內問題，確認回覆正確且附 `來源：` 頁尾。

---

## 審計規則（verify_index.py）

驗收對象是「候選 `kb_run_id`」，需全部通過：

1. **數量一致（以 `kb_run_id` 篩選）**  
   - 期望 chunk 數：加總本次處理文件的 chunks（manifest 或 chunks json）
   - 實際 chunk 數：Chroma data collection where `kb_run_id == candidate`

2. **無重複 ID（在該 `kb_run_id` 範圍內）**

3. **黃金測試集命中率 ≥ 80%**  
   - 對 `tests/golden_qa.yaml` 逐題檢索 top_n（由 `pipeline.yaml` 控制）
   - 檢查 top results metadata 是否包含 `expected_doc_id`

> verify 失敗：exit code ≠ 0，且 **不得** 更新 active 指標（= 不發布）。

---

## Telegram Bot（只讀查詢）

Bot 流程：

1. 讀 Chroma control collection 的 `active_kb_run_id`
2. 檢索 Chroma data collection（where: `kb_run_id == active_kb_run_id`）
3. 組 prompt → 呼叫 Groq → 回覆（含引用來源：`source_file` / `doc_id` 等）

### Bot 存取控制（可選）
- 預設（測試模式）：**不限制**（未設定 `BOT_ALLOWLIST`）
- 若設定 `BOT_ALLOWLIST`（逗號分隔 id），則只允許名單內 `chat_id` 或 `user_id`

---

## Definition of Done（驗收標準）

- [ ] 任一文件解析失敗不會讓整批 pipeline crash（parser 規則）
- [ ] indexer 寫入後，verify 能針對候選 `kb_run_id` 完成三項稽核
- [ ] verify 失敗時，publisher 不會執行，Bot 不會切到新版本
- [ ] verify 通過後，publisher 更新 `active_kb_run_id`，Bot 查詢只命中該版本
- [ ] golden_qa 命中率 ≥ 80%

---

## 已知限制（本階段承認但不解）

- Chroma Cloud 免費額度用盡需人工處理（未做自動監控/攔截）
- 做法 B 會累積舊 `kb_run_id` 資料：未提供自動清理舊版本機制
- Render free tier 可能 idle sleep 造成冷啟動延遲

---

## 貢獻與變更原則（重要）

- 腳本採「**單一職責**」與「**檔案交接**」：每階段輸出資料檔 + manifest，禁止用記憶體物件偷串流程。
- 控制面（`config/pipeline.yaml`）與密鑰完全分離。
- 請避免「順手重構」非本需求相關程式碼；每個改動要能追溯到需求或驗收標準。

---

## License

依本 repo 設定（若尚未提供，請補上 LICENSE）。
```
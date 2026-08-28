# System Map — 企業 RAG 知識庫 + Telegram Bot（MVP, Chroma Cloud + 版本發布做法 B）

## 一句話定位
以繁體中文文件/問答為主，把企業內部文件（PDF/Word/Excel）透過排程流水線
解析 → 切塊 → 向量化 → 寫入 Chroma Cloud，並用 Telegram Bot 提供可引用來源的檢索式問答（RAG）。
同時採用「做法 B」：以 `kb_run_id` 標記版本，僅將通過審計的版本設為「已發布」。

---

## 架構總覽（出版流水線對應）

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
        │ indexer.py 寫入「候選版本」到 Chroma Cloud（資料集合）
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

> 重點：verify 失敗時，資料可能已寫入 Chroma Cloud，但因為 **active_kb_run_id 不會更新**，
> Bot 只會查「已發布版本」，等同「審核不過＝不發布」。

---

## 元件清單（單一職責）

| 元件 | 單一職責 | 輸入 | 輸出 |
|------|----------|------|------|
| `doc_classifier.py` | 規則式判斷文件 🟢/🔴（可自動/需人工） | `data/raw/**` + `pipeline.yaml` | `pending_queue.json` |
| `doc_watcher.py` | 找出新增/變更文件（上限 20 份/次） | `pending_queue.json` | 本次待處理清單 |
| `parser.py` | Unstructured 解析單一文件 | raw file | `data/parsed/{doc_id}.json` + manifest |
| `chunker.py` | hierarchical chunking + metadata | parsed json + manifest | `data/chunks/{doc_id}.json` + manifest |
| `embedder.py` | 產生向量（以中文為主的 embedding model） | chunks json | `data/embeddings/{doc_id}.npy` + manifest |
| `indexer.py` | 寫入 Chroma Cloud（候選 kb_run_id） | embeddings + chunks metadata | Chroma Cloud（data collection）+ 更新 status |
| `verify_index.py` | 獨立審計（只讀）：驗證候選 kb_run_id 的品質 | Chroma Cloud + golden_qa | 通過/中止 + 寫 status |
| `publisher.py` | 發布：通過審計才更新 active_kb_run_id | 審計通過訊號 + kb_run_id | Chroma Cloud（control collection）+ status |
| `bot.py` | Telegram 問答（只讀）：讀 active_kb_run_id → 檢索 → Groq | 使用者訊息 | 回覆（含來源） |

---

## 目錄結構（Repo）

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

## 執行環境 / 外部服務

| 元件 | 執行位置 | 常駐 | 備註 |
|------|----------|------|------|
| Pipeline（classifier→publisher） | GitHub Actions | 否 | 排程批次、concurrency lock |
| Bot | Render free tier | 是 | 可能 idle sleep |
| 向量庫 | Chroma Cloud（Starter） | 是 | data/control 兩個 collections |
| LLM | Groq | 是 | 只在 Bot 端呼叫 |

---

## 密鑰管理（不進 repo）

| 變數 | 用途 | 放哪裡 |
|------|------|--------|
| `CHROMA_API_KEY` `CHROMA_TENANT` `CHROMA_DATABASE` | Actions 寫入/審計 + Bot 查詢 | GitHub Secrets + Render env |
| `GROQ_API_KEY` | Bot 生成回答 | Render env |
| `TELEGRAM_BOT_TOKEN` | Bot | Render env |
| `BOT_ALLOWLIST`（可選） | 最小安全護欄（允許的 chat_id / user_id） | Render env（預設不設＝不限制，符合測試模式） |

---

## 十一思維落點索引
- 分而治之：腳本拆分 + 檔案交接
- 結構化通訊：manifest / status schema
- 控制面分離：pipeline.yaml
- 零信任審計：verify_index.py（只讀）
- 防禦性設計：concurrency lock、每次最多 20 份
- 實證反饋：golden_qa 命中率 + Action Summary + kb_status.json
- 做法 B（發布）：kb_run_id + active pointer（control collection）

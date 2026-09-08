# Phase 4 證據報告 — bot.py

- 日期：2026-09-08
- Branch：`phase4-bot`
- 依據：spec.md §5.9（bot.py 只讀問答 + 最小護欄）、§4.2（active 指標）、§7（pipeline.yaml 控制面）

## 假設（請確認）

1. **active 指標「每次查詢前」讀取**：spec §4.2 寫「Bot 啟動/每次查詢前，會讀取 active_kb_run_id」，斜線兩種解讀都有道理。我採「每次查詢前都讀」（`answer_question()` 內讀取），好處是 publisher 發布新版本後 Bot 立即生效，不用重開程序；代價是每則訊息多一次 control collection 讀取。若你要「啟動時讀一次快取」，請告知，我改。
2. **來源以「確定性頁尾」附上**：§5.9 只要求「回覆需附來源（source_file/doc_id）」。prompt 裡也指示 LLM 引用來源，但為保證回覆一定有來源，我在 LLM 回覆後附加固定格式的 `來源：` 頁尾（source_file + doc_id）。
3. **不在名單內的處理方式 = 靜默忽略**：§5.9 未規範被拒時要不要回覆訊息。我選「不回覆」（直接 return），不回應陌生人、也不洩漏 bot 存在與否。
4. **查無資料時不回 call Groq**：檢索不到任何 chunk 時直接回「知識庫中查無相關資料。」，不消耗 LLM 額度。
5. **`llm.daily_request_cap` 未在 bot 端強制**：§7 控制面有這個欄位，但 §5.9 未要求 bot 實作計數/攔截（§2 也說自動用量監控 Out of Scope），故未實作。
6. Groq 呼叫參數最小化：單輪 `chat.completions.create`、只用 pipeline.yaml 的 `llm.model`，未加 temperature 等 spec 未要求的參數。

## 做了什麼

新增 `bot.py`（無修改任何既有模組；`requirements.txt` 僅「新增」兩行 `python-telegram-bot`、`groq`）：

1. `get_active_kb_run_id()`：自 control collection 固定 id（`control_pointer_id`，預設 `active_pointer`）讀取 `active_kb_run_id`；指標不存在 → `RuntimeError`。
2. `retrieve_chunks()`：對 data collection 下 `where={"kb_run_id": active_kb_run_id}`，`n_results=retrieval.top_k`（pipeline.yaml），回傳 chunk text + metadata。
3. `build_prompt()` / `format_sources()`：繁中 prompt（只准依參考資料回答、不足要說明），chunk 以 `[n] 來源：<source_file>（doc_id: ...）` 標註；回覆後附確定性 `來源：` 頁尾。
4. `answer_question()`：串起 1→2→3， Groq 呼叫可注入 `chat_fn`（測試用 fake，不走網路）。
5. `is_allowed()`：`BOT_ALLOWLIST` 未設定或空白 → 全部允許；有設定 → 逗號分隔 id，chat_id **或** user_id 命中即允許。
6. `main()`：python-telegram-bot v21 polling，`filters.TEXT & ~filters.COMMAND` handler 先做 allowlist 檢查再走 `answer_question()`；例外只印 stderr、回通用錯誤訊息，不洩漏內部細節。

測試：`tests/test_bot.py`（9 項，全部本地 ephemeral chromadb + 注入 fake embed/chat，無 Chroma Cloud / Telegram / Groq 真實請求）。

## 怎麼驗證（實際輸出）

### 1. 自動化測試

```
$ python -m pytest tests/test_bot.py -v
tests/test_bot.py::test_retrieval_where_filter_only_returns_active_kb_run_id PASSED
tests/test_bot.py::test_retrieve_chunks_respects_where_filter_directly PASSED
tests/test_bot.py::test_get_active_kb_run_id_missing_pointer_raises PASSED
tests/test_bot.py::test_allowlist_unset_allows_anyone PASSED
tests/test_bot.py::test_allowlist_empty_string_allows_anyone PASSED
tests/test_bot.py::test_allowlist_set_restricts_by_chat_id PASSED
tests/test_bot.py::test_allowlist_set_matches_user_id_too PASSED
tests/test_bot.py::test_build_prompt_contains_source_info PASSED
tests/test_bot.py::test_no_chunks_replies_without_calling_llm PASSED
9 passed
```

全量測試：`56 passed, 1 failed` — 唯一失敗 `tests/test_schemas.py::test_golden_qa_yaml_is_loadable` 為**既有問題**（phase 3c 報告已記錄：golden_qa.yaml 現有 14 題、該測試斷言 3 題），與本相位無關，未動手修改（依硬性規則 5）。

### 2. where filter 只命中 active 版本（本地 ephemeral chromadb，實際輸出）

情境：data collection 內有兩個版本共 3 筆 chunk（active=`20260908-090000` 兩筆、舊版 `20260907-090000` 一筆），control collection 的 active pointer 指向 `20260908-090000`：

```
active_kb_run_id = 20260908-090000
where 篩選後回傳 chunk 數（top_k=5，全庫共 3 筆）： 2
  - 20260908-090000 docA 新版請假辦法.pdf | 新版：特休十天
  - 20260908-090000 docA 新版請假辦法.pdf | 新版：產假八週
```

top_k=5 > 全庫 3 筆——若 where 沒生效，舊版那筆「舊版：特休七天」必然被回傳；實際只回傳 active 版本的 2 筆，舊版被濾除。

### 3. 完整回覆含來源（fake chat_fn 的實際輸出）

```
依據新版辦法，特休十天。

來源：
- 新版請假辦法.pdf（doc_id: docA）
- 新版請假辦法.pdf（doc_id: docA）
```

### 4. allowlist（實際輸出）

```
未設定: 123 -> True | 456 -> True
="123": chat 123 -> True | chat 456 -> False
```

### 5. 人工測試紀錄（真實 Telegram/Groq）

**此項無法在本環境執行**：sandbox 沒有 `TELEGRAM_BOT_TOKEN` / `GROQ_API_KEY` / Chroma Cloud 憑證，且硬性規則禁止對正式服務做自動化測試。需要你在 Render（或本機）注入環境變數後以真人帳號對話驗證，確認：

1. 私訊 bot 一個知識庫內的問題 → 回覆正確且附 `來源：` 頁尾；
2. 問知識庫外的問題 → 回「知識庫中查無相關資料。」；
3. 設定 `BOT_ALLOWLIST` 後，名單外帳號發訊息 → 無任何回覆。

## DoD 對照

- [x] 本地 ephemeral chromadb 造 active_kb_run_id → where filter 只查到該版本（見上方實際輸出：top_k=5 全庫 3 筆只回 2 筆 active 版本）
- [x] `BOT_ALLOWLIST` 未設定 → 任何 chat_id 都通過（`123 -> True | 456 -> True`）
- [x] `BOT_ALLOWLIST="123"` → chat_id=123 通過、456 被拒（`chat 123 -> True | chat 456 -> False`）
- [ ] 真實 Telegram/Groq 真人對話截圖：**無法在此環境執行**，需部署後人工驗證（見「人工測試紀錄」節）

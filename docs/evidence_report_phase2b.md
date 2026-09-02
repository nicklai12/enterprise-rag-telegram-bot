# Phase 2b 證據報告 — chunker.py

## 1. 本次實作內容

- 新增 `chunker.py`：
  - 讀取 parsed document JSON（正式：`data/parsed/{doc_id}.json`；開發/測試預設：`data/fixtures/sample_parsed.json`）。
  - 依 `config/pipeline.yaml:chunking` 的 `chunk_size` / `overlap` 做 hierarchical chunking（`strategy` 目前僅 `hierarchical` 一種，見 §2 假設）。
  - 若 parsed JSON 旁存在 `{doc_id}.manifest.json`（parser 階段產生），讀取並把 `parse_errors` 延續到 chunker manifest。
  - 輸出 `data/chunks/{doc_id}.json`（符合 `schemas/chunks.schema.json`）+ `data/chunks/{doc_id}.manifest.json`（`stage="chunker"`，含 `chunk_count`）。
  - CLI：`python chunker.py [--parsed ...] [--config ...] [--out-dir ...]`，預設即對 fixture 跑一次。
- 新增 `tests/test_chunker.py`：6 個測試（見 §3.1）。

## 2. 實作假設與解讀（請你確認）

| 項目 | 本實作選擇 | 其他可能解讀 |
|---|---|---|
| hierarchical chunking 的定義 | 尊重 element/段落邊界：連續 element 以字元數貪婪合併（合計 ≤ `chunk_size`），遇到 Title 視為新 section（關閉目前 chunk、重置 overlap 情境），每個 chunk 的 metadata 記錄最近的 section 標題。單一 element 超過 `chunk_size` 時才按字元硬切，並以 `overlap × chunk_size` 的字元重疊；跨 chunk 亦帶入前 chunk 尾端 overlap 字元。 | 也可導入 LlamaIndex 的 `HierarchicalNodeParser`（small-to-big、父子節點），但 spec §3 雖列 LlamaIndex 為技術棧，§5.4 未要求特定套件，最小實作先以規則式完成；之後 embedder/retrieval 階段若要换成 LlamaIndex 可再調整。 |
| 計量單位 | `chunk_size` 以**字元數**計（繁中文件字元≈token 概念直覺）。 | 也可按 token（需 tokenizer 依賴），spec 未指定。 |
| `strategy` 欄位 | 讀取但不分支（目前僅支援 `hierarchical`），不為未支援的策略加抽象層。 | 可做成策略分派，但 spec 只有一種策略，屬過度設計。 |
| manifest 檔名 | system-map 寫「`data/chunks/{doc_id}.json + .manifest.json`」，解讀為同目錄的 `{doc_id}.manifest.json`。 | 也可能指共用單一 `manifest.json`；採前者以維持逐文件交接。 |
| manifest 額外欄位 | 不記錄 chunk_size/overlap 等參數，因 `schemas/manifest.schema.json` 為 `additionalProperties: false`，加欄位會驗證失敗。 | 若要在 manifest 留參數，需先改 manifest schema（超出本任務範圍）。 |
| 「+ manifest」輸入 | parser manifest 存在才讀（沿用 `parse_errors`）；不存在不報錯（parser.py 屬 phase 2a 尚未實作，開發期常缺席）。doc_id/source_file/department 一律以 parsed JSON 為準。 | 也可要求 manifest 必填，但會阻塞目前的 fixture 開發流程。 |

## 3. 驗證方式

### 3.1 單元測試

```bash
pytest tests/test_chunker.py -v
```

實際輸出：

```text
tests/test_chunker.py::test_fixture_run_chunk_count_matches_and_schema_valid PASSED [ 16%]
tests/test_chunker.py::test_chunk_count_changes_with_chunk_size PASSED   [ 33%]
tests/test_chunker.py::test_chunk_texts_respect_chunk_size PASSED        [ 50%]
tests/test_chunker.py::test_overlap_is_applied_between_split_pieces PASSED [ 66%]
tests/test_chunker.py::test_title_becomes_section_context PASSED         [ 83%]
tests/test_chunker.py::test_parse_errors_carried_over_from_parser_manifest PASSED [100%]

============================== 6 passed in 2.07s ===============================
```

### 3.2 fixture 實際執行（預設 pipeline.yaml，chunk_size=512）

`python chunker.py --parsed data/fixtures/sample_parsed.json --out-dir data/chunks`

產生 `data/chunks/sample.json`（chunks 陣列長度 **1**）與 `data/chunks/sample.manifest.json`：

```json
{
  "doc_id": "sample",
  "source_file": "data/raw/HR/sample_raw.txt",
  "department": "HR",
  "status": "chunked",
  "timestamp": "2026-09-02T14:39:09.567042+00:00",
  "parse_errors": [],
  "stage": "chunker",
  "chunk_count": 1
}
```

chunks 內容：

```json
{
  "chunk_index": 0,
  "text": "人力資源部請假規則\n一、特休。員工每滿六個月可享三日特休，滿一年起依勞基法計算。\n二、事假。因私事需處理者，每次至少半日，全年累計不得超過十四日。\n三、病假。檢附醫療證明，全年不得超過三十日。超過部分依公司規定辦理。",
  "metadata": {
    "source_file": "data/raw/HR/sample_raw.txt",
    "department": "HR",
    "section": "人力資源部請假規則"
  }
}
```

### 3.3 兩種 chunk_size 比較（證明讀設定檔，非寫死）

以臨時 config 只改 `chunk_size` 重跑同一 fixture（正式 `config/pipeline.yaml` 未更動）：

| 設定 | chunk_count（manifest） | chunks 陣列長度 |
|---|---|---|
| `chunk_size: 512`（repo 預設） | 1 | 1 |
| `chunk_size: 40`（臨時 config） | 3 | 3 |

`chunk_size: 40` 實際輸出：

```text
chunk_size=40 -> chunk_count = 3
[0] (40 chars) 人力資源部請假規則\n一、特休。員工每滿六個月可享三日特休，滿...
[1] (41 chars) 起依勞基法計算。\n二、事假。因私事需處理者，每次至少半日，全...
[2] (43 chars) 不得超過十四日。\n三、病假。檢附醫療證明，全年不得超過三十日...
manifest chunk_count = 3
```

（chunk 1、2 略大於 40 字元，是 overlap 尾字元帶入所致：40 + int(40×0.2)=8 的上限內。）

### 3.4 Schema 驗證

以 `jsonschema` 對兩種設定下的輸出實際驗證：

```text
chunks schema OK: data/chunks/sample.json
chunks schema OK: /tmp/chunks_small/sample.json
manifest schema OK: data/chunks/sample.manifest.json
manifest schema OK: /tmp/chunks_small/sample.manifest.json
```

## 4. 驗收標準 DoD 對照

- [x] 用 fixture 跑一次，chunk_count 與輸出陣列長度一致（512 → 皆為 1；40 → 皆為 3，見 §3.2/§3.3）。
- [x] 改 pipeline.yaml 的 chunk_size 後重跑，chunk 數量隨之變化（512 → 1 chunk，40 → 3 chunks，見 §3.3）。
- [x] 輸出通過 `schemas/chunks.schema.json` 驗證（manifest 亦通過 `manifest.schema.json`，見 §3.4）。
- [x] 證據報告附上兩種 chunk_size 設定下的輸出比較（見 §3.3）。

## 5. 已知但本次未處理的項目

- 執行 `pytest tests/` 時，`tests/test_schemas.py::test_golden_qa_yaml_is_loadable` 失敗（golden_qa.yaml 現有 14 題、測試預期 3 題）。此為 phase 1b 報告已記錄的既有問題，不在本任務範圍，未修改。

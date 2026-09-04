# 證據報告 — Phase 2c: embedder.py

## 做了什麼

依 spec.md §5.5 實作 `embedder.py`，並以 `chunker.py` 的既有風格/結構為模板：

1. 讀取 chunks JSON（預設 dev fixture `data/chunks/sample.json`，可用 `--chunks` 指定），
   依 `config/pipeline.yaml:embedding.model`（預設 `BAAI/bge-small-zh-v1.5`）與
   `batch_size` 以 sentence-transformers 產生向量。
2. 輸出 `data/embeddings/{doc_id}.npy`（`np.float32`，每列對應一個 chunk）
   + `{doc_id}.manifest.json`，`stage="embedder"`，記錄
   `embedding_model` / `embedding_dim`（取自模型實際輸出 `vectors.shape[1]`，未寫死）/
   `vector_count`。
3. row → chunk 的對應寫進 manifest 的 `chunk_order`（依
   `schemas/embeddings_manifest.schema.json` 約定：第 i 列 = `chunks[chunk_order[i]]`），
   不靠順序隱性約定；`parse_errors` 沿用 chunker 的做法從 chunks manifest 攜帶。
4. 新增 `tests/test_embedder.py`（4 個測試）與 requirements.txt 一行
   `sentence-transformers>=3.0,<5.0`。

## 假設與說明（請確認）

- **manifest `status` 值**：spec §6 未規定 embedder 階段的 status 字串，我採用
  `status="embedded"`（與 chunker 的 `"chunked"` 命名模式一致）。若你想用別的值
  （例如 `"ok"`）請告知。
- **模型快取**：`get_model()` 在程序內快取 SentenceTransformer 實例，避免多份文件
  重複載入模型；不改變任何輸出語意。
- 測試使用真實模型（本機執行，已下載至 HF cache），未對 Chroma Cloud / Telegram /
  Groq 發送任何請求。

## 怎麼驗證（實際輸出）

### 1. pytest（本模組 4 項全過）

```
tests/test_embedder.py::test_fixture_run_npy_rows_match_chunk_count PASSED
tests/test_embedder.py::test_npy_row_count_and_dim_match_chunks PASSED
tests/test_embedder.py::test_chunk_order_maps_rows_to_chunk_index PASSED
tests/test_embedder.py::test_parse_errors_carried_over_from_chunker_manifest PASSED
4 passed in 14.13s
```

測試涵蓋：fixture 跑完 row 數 = chunk 數、manifest 過
`embeddings_manifest.schema.json` 驗證、`embedding_dim == 模型實際輸出維度 ==
npy.shape[1]`、`chunk_order` 在非連續 chunk_index（[7,3,9]）下仍正確記錄、
parse_errors 攜帶。

### 2. CLI 實跑（`python embedder.py`，3-chunk 固定 fixture 之外另用 dev fixture）

```
{
  "doc_id": "sample",
  "source_file": "data/raw/HR/sample_raw.txt",
  "department": "HR",
  "status": "embedded",
  "timestamp": "2026-09-04T13:17:51.543117+00:00",
  "parse_errors": [],
  "stage": "embedder",
  "embedding_model": "BAAI/bge-small-zh-v1.5",
  "embedding_dim": 512,
  "vector_count": 1,
  "chunk_order": [0]
}
```

另以 3-chunk fixture 實測（test 內執行）：`npy.shape = (3, 512)`、
`vector_count = 3`、`chunk_order = [0, 1, 2]`。

### 3. 實際數值

- **embedding_dim = 512**（`BAAI/bge-small-zh-v1.5` 模型實際輸出，程式碼由
  `vectors.shape[1]` 取得，無寫死）
- **npy.shape = (1, 512)**（dev fixture，1 個 chunk）；3-chunk fixture 為 **(3, 512)**

### 4. 全量測試

`python -m pytest tests/ -q` → `1 failed, 33 passed`。失敗的
`tests/test_schemas.py::test_golden_qa_yaml_is_loadable` 在乾淨的 main（未含本次
任何改動，`git stash` 驗證）下同樣失敗，屬既有問題，依規則不動手修改，僅在此回報。

## 驗收標準 DoD 逐項

- [x] 用 fixture（2~3 個 chunk）跑一次 → npy 的 row 數 = chunk 數：
      3-chunk fixture 實測 `npy.shape[0] == 3 == vector_count`（test_npy_row_count_and_dim_match_chunks）
- [x] manifest 的 embedding_dim 與模型實際輸出維度一致（不可寫死）：
      `embedding_dim == model.encode(...).shape[1] == vectors.shape[1] == 512`（程式由 shape 取得）
- [x] 證據報告附上實際 embedding_dim 數值與 npy.shape：embedding_dim=512，
      npy.shape=(1, 512)（dev fixture）/ (3, 512)（3-chunk fixture）

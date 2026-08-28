# Phase 0 證據報告：契約與骨架

## 本次交付內容

1. `config/pipeline.yaml`：完全複製 spec.md §7 範例，未增減 key。
2. `schemas/manifest.schema.json`：必填 `doc_id`、`source_file`、`department`、`status`、`timestamp`、`parse_errors`；選填 §6 列出的 stage-specific 欄位。
3. 自訂 schema（spec 未明確定義）：
   - `schemas/pending_queue.schema.json`
   - `schemas/status.schema.json`
   - `schemas/parsed_doc.schema.json`
   - `schemas/chunks.schema.json`
   - `schemas/embeddings_manifest.schema.json`
4. `data/fixtures/sample_raw.txt`、`sample_parsed.json`、`sample_chunks.json`：假資料鏈。
5. `tests/golden_qa.yaml`：TODO 骨架 3 題，頂端含警示註解。
6. `status/kb_status.json`：空 pipeline 狀態模板。
7. 空目錄骨架 `data/raw/HR/`、`data/parsed/`、`data/chunks/`、`data/embeddings/`（`.gitkeep`）。
8. `tests/test_schemas.py`：自動化測試。
9. `requirements.txt`：新增 `jsonschema`、`PyYAML`、`pytest` 三行。

## 設計假設（需要確認）

spec.md 對以下格式未給明確定義，這是我採用的最小設計：

- `pending_queue.schema.json`：採用**陣列**結構，每筆文件包含 `doc_id`、`source_file`、`department`、`decision`（`auto_process` / `needs_review`），可選 `reason`。理由：classifier 只輸出 queue，watcher 後續可根據 `decision` 過濾，陣列最簡單。
- `status.schema.json`：頂層物件含 `updated_at`、`active_kb_run_id`、`runs`。`runs` 是陣列，每筆記錄 `kb_run_id`、`started_at`、`finished_at`、`status`、`summary`。理由：空狀態只需 `active_kb_run_id: null` 與空 `runs`，後續 pipeline 可追加 run 記錄。
- `parsed_doc.schema.json`：沿用 Unstructured element list 格式，包含 `doc_id`、`source_file`、`department` 與 `elements`，每個 element 至少 `type` 與 `text`。
- `chunks.schema.json`：包含 `doc_id`、`source_file`、`department` 與 `chunks` 陣列；每個 chunk 含 `chunk_index`、`text` 與 `metadata`（至少 `source_file`、`department`）。
- `embeddings_manifest.schema.json`：在 generic manifest 必填欄位外，額外要求 `stage="embedder"`、`embedding_model`、`embedding_dim`、`vector_count` 與 `chunk_order`。`chunk_order` 是一個與 `.npy` 列數等長的整數陣列，第 i 列對應 `chunks[chunk_order[i]]`。理由：最小化地記錄向量列與 chunk_index 的對應關係。

## 驗證指令與實際輸出

### 1. 執行自動化測試

```bash
pytest -q
```

輸出：

```text
.........                                                                [100%]
9 passed in 2.22s
```

### 2. 用 jsonschema 直接驗證 fixture

```bash
python3 - <<'PY'
import json, pathlib
import jsonschema
root = pathlib.Path('.')
for name, fixture in [
    ('parsed_doc', root/'data/fixtures/sample_parsed.json'),
    ('chunks', root/'data/fixtures/sample_chunks.json'),
]:
    schema = json.loads((root/'schemas'/f'{name}.schema.json').read_text())
    data = json.loads(fixture.read_text())
    jsonschema.validate(instance=data, schema=schema)
    print(f'✓ {fixture} passed {name}.schema.json')
print('All fixture validations passed.')
PY
```

輸出：

```text
✓ data/fixtures/sample_parsed.json passed parsed_doc.schema.json
✓ data/fixtures/sample_chunks.json passed chunks.schema.json
All fixture validations passed.
```

### 3. 確認 pipeline.yaml 含 spec §7 全部 key

```bash
python3 - <<'PY'
import yaml
with open('config/pipeline.yaml', 'r', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)
required = {'watch_folders','auto_process_rules','chunking','embedding','vectorstore','llm','retrieval'}
missing = required - cfg.keys()
print('Config keys present:', sorted(cfg.keys()))
print('Missing required keys:', sorted(missing))
assert not missing
PY
```

輸出：

```text
Config keys present: ['auto_process_rules', 'chunking', 'embedding', 'llm', 'retrieval', 'vectorstore', 'watch_folders']
Missing required keys: []
```

## 驗收標準 DoD 自評

- [x] 每個 `schemas/*.json` 都能被 python jsonschema 套件成功載入（合法 JSON Schema）
- [x] 每個 fixture 檔都能通過對應 schema 的驗證
- [x] `config/pipeline.yaml` 能被 `yaml.safe_load` 讀出且含 spec §7 全部 key
- [x] 證據報告附上「用 jsonschema 驗證 fixture 通過」的實際指令與輸出

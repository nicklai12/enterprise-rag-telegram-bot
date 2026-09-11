"""Tests for bot.py (spec §5.9).

All tests run against a local ephemeral ChromaDB with injected embed/chat
callables — no Chroma Cloud / Telegram / Groq network calls.

Covers the three pure-logic behaviors required by the task:
1. the retrieval where filter only returns chunks of the active kb_run_id;
2. allowlist logic with BOT_ALLOWLIST set vs. unset;
3. the assembled prompt carries the source info (source_file / doc_id).
"""
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chromadb
import yaml

import bot

CONFIG_PATH = ROOT / "config" / "pipeline.yaml"
POINTER_ID = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))[
    "vectorstore"
]["control_pointer_id"]

ACTIVE_KB_RUN_ID = "20260908-090000"
OLD_KB_RUN_ID = "20260907-090000"


def _build_ephemeral_setup():
    """Ephemeral control+data collections holding two kb_run_id versions."""
    client = chromadb.EphemeralClient()
    control = client.create_collection(f"company_kb_control_{uuid.uuid4().hex}")
    data = client.create_collection(f"company_kb_data_{uuid.uuid4().hex}")
    control.upsert(
        ids=[POINTER_ID],
        metadatas=[{"active_kb_run_id": ACTIVE_KB_RUN_ID}],
        documents=[f"active pointer -> kb_run_id {ACTIVE_KB_RUN_ID}"],
    )
    data.upsert(
        ids=[
            f"{ACTIVE_KB_RUN_ID}_doc_new_0",
            f"{ACTIVE_KB_RUN_ID}_doc_new_1",
            f"{OLD_KB_RUN_ID}_doc_old_0",
        ],
        embeddings=[[1.0, 0.0], [0.0, 1.0], [0.894, 0.447]],
        metadatas=[
            {
                "kb_run_id": ACTIVE_KB_RUN_ID,
                "doc_id": "doc_new",
                "chunk_index": 0,
                "source_file": "新版的請假辦法.pdf",
                "department": "HR",
            },
            {
                "kb_run_id": ACTIVE_KB_RUN_ID,
                "doc_id": "doc_new",
                "chunk_index": 1,
                "source_file": "新版的請假辦法.pdf",
                "department": "HR",
            },
            {
                "kb_run_id": OLD_KB_RUN_ID,
                "doc_id": "doc_old",
                "chunk_index": 0,
                "source_file": "舊版請假辦法.pdf",
                "department": "HR",
            },
        ],
        documents=["新版內容：特休十天", "新版內容：產假八週", "舊版內容：特休七天"],
    )
    return control, data


def _fake_embed(texts):
    return [[1.0, 0.0] for _ in texts]


def test_retrieval_where_filter_only_returns_active_kb_run_id():
    """Active pointer = ACTIVE_KB_RUN_ID: only that version's chunks are hit.

    top_k (5) exceeds the number of active chunks (2), so without the
    where filter the old-version chunk would also be returned; this test
    proves the filter excludes it.
    """
    control, data = _build_ephemeral_setup()
    captured = {}

    def fake_chat(prompt, model):
        captured["prompt"] = prompt
        captured["model"] = model
        return "根據新版辦法，特休為十天。"

    answer = bot.answer_question(
        "特休有幾天？",
        config=bot.load_config(CONFIG_PATH),
        control_collection=control,
        data_collection=data,
        embed_fn=_fake_embed,
        chat_fn=fake_chat,
    )

    active_kb_run_id = bot.get_active_kb_run_id(control, POINTER_ID)
    assert active_kb_run_id == ACTIVE_KB_RUN_ID
    # answer footer cites only the active version's sources
    assert "新版的請假辦法.pdf" in answer
    assert "doc_new" in answer
    assert "舊版請假辦法.pdf" not in answer
    assert "舊版內容" not in answer
    # prompt handed to Groq contains the question and source-tagged chunks
    assert "特休有幾天？" in captured["prompt"]
    assert "新版的請假辦法.pdf" in captured["prompt"]
    assert "doc_new" in captured["prompt"]
    assert "新版內容：特休十天" in captured["prompt"]
    assert "舊版" not in captured["prompt"]
    assert captured["model"] == "llama-3.1-8b-instant"


def test_retrieve_chunks_respects_where_filter_directly():
    """Direct check: every returned chunk's metadata is the active kb_run_id."""
    _, data = _build_ephemeral_setup()
    chunks = bot.retrieve_chunks(data, ACTIVE_KB_RUN_ID, [1.0, 0.0], top_k=5)
    assert len(chunks) == 2
    assert all(
        chunk["metadata"]["kb_run_id"] == ACTIVE_KB_RUN_ID for chunk in chunks
    )
    # and querying the OLD id returns the old chunk, proving filter selectivity
    old_chunks = bot.retrieve_chunks(data, OLD_KB_RUN_ID, [1.0, 0.0], top_k=5)
    assert [c["metadata"]["kb_run_id"] for c in old_chunks] == [OLD_KB_RUN_ID]


def test_get_active_kb_run_id_missing_pointer_raises():
    client = chromadb.EphemeralClient()
    control = client.create_collection(f"empty_control_{uuid.uuid4().hex}")
    try:
        bot.get_active_kb_run_id(control, POINTER_ID)
    except RuntimeError as exc:
        assert POINTER_ID in str(exc)
    else:
        raise AssertionError("expected RuntimeError for missing active pointer")


def test_allowlist_unset_allows_anyone(monkeypatch):
    monkeypatch.delenv("BOT_ALLOWLIST", raising=False)
    assert bot.is_allowed(chat_id=123, user_id=111) is True
    assert bot.is_allowed(chat_id=456, user_id=222) is True
    assert bot.is_allowed(chat_id=None, user_id=None) is True


def test_allowlist_empty_string_allows_anyone(monkeypatch):
    monkeypatch.setenv("BOT_ALLOWLIST", "")
    assert bot.is_allowed(chat_id=456, user_id=222) is True


def test_allowlist_set_restricts_by_chat_id(monkeypatch):
    monkeypatch.setenv("BOT_ALLOWLIST", "123")
    assert bot.is_allowed(chat_id=123, user_id=111) is True
    assert bot.is_allowed(chat_id=456, user_id=222) is False


def test_allowlist_set_matches_user_id_too(monkeypatch):
    monkeypatch.setenv("BOT_ALLOWLIST", "123, 789")
    assert bot.is_allowed(chat_id=456, user_id=123) is True
    assert bot.is_allowed(chat_id=456, user_id=789) is True
    assert bot.is_allowed(chat_id=456, user_id=555) is False


def test_build_prompt_contains_source_info():
    chunks = [
        {
            "text": "chunk 內容",
            "metadata": {
                "kb_run_id": ACTIVE_KB_RUN_ID,
                "doc_id": "doc_new",
                "chunk_index": 0,
                "source_file": "新版的請假辦法.pdf",
                "department": "HR",
            },
        }
    ]
    prompt = bot.build_prompt("特休有幾天？", chunks)
    assert "新版的請假辦法.pdf" in prompt
    assert "doc_new" in prompt
    assert "chunk 內容" in prompt
    assert "特休有幾天？" in prompt


def test_no_chunks_replies_without_calling_llm():
    control, data = _build_ephemeral_setup()
    control.upsert(
        ids=[POINTER_ID],
        metadatas=[{"active_kb_run_id": "20990101-000000"}],
        documents=["active pointer -> kb_run_id 20990101-000000"],
    )
    called = []

    def fake_chat(prompt, model):
        called.append(prompt)
        return "should not be called"

    answer = bot.answer_question(
        "完全不存在的問題",
        config=bot.load_config(CONFIG_PATH),
        control_collection=control,
        data_collection=data,
        embed_fn=_fake_embed,
        chat_fn=fake_chat,
    )
    assert called == []
    assert "查無相關資料" in answer


def test_groq_model_env_var_overrides_yaml(monkeypatch):
    """GROQ_MODEL env var wins over pipeline.yaml llm.model."""
    control, data = _build_ephemeral_setup()
    captured = {}

    def fake_chat(prompt, model):
        captured["model"] = model
        return "回答"

    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    bot.answer_question(
        "特休有幾天？",
        config=bot.load_config(CONFIG_PATH),
        control_collection=control,
        data_collection=data,
        embed_fn=_fake_embed,
        chat_fn=fake_chat,
    )
    assert captured["model"] == "llama-3.3-70b-versatile"


def test_groq_model_falls_back_to_yaml_when_env_unset_or_empty(monkeypatch):
    """Unset/empty GROQ_MODEL → llm.model from pipeline.yaml is used."""
    control, data = _build_ephemeral_setup()
    captured = {}

    def fake_chat(prompt, model):
        captured["model"] = model
        return "回答"

    config = bot.load_config(CONFIG_PATH)
    expected = config["llm"]["model"]

    monkeypatch.delenv("GROQ_MODEL", raising=False)
    bot.answer_question(
        "特休有幾天？",
        config=config,
        control_collection=control,
        data_collection=data,
        embed_fn=_fake_embed,
        chat_fn=fake_chat,
    )
    assert captured["model"] == expected

    monkeypatch.setenv("GROQ_MODEL", "")
    bot.answer_question(
        "特休有幾天？",
        config=config,
        control_collection=control,
        data_collection=data,
        embed_fn=_fake_embed,
        chat_fn=fake_chat,
    )
    assert captured["model"] == expected

"""bot.py: read-only Telegram RAG bot (spec §5.9).

Query flow:
1. Read ``active_kb_run_id`` from the Chroma control collection
   (fixed id ``vectorstore.control_pointer_id``, default ``active_pointer``).
2. Retrieve from the data collection with the where filter
   ``kb_run_id == active_kb_run_id`` (top_k from ``retrieval.top_k``).
3. Build a prompt with the retrieved chunks + their sources, call Groq
   (``llm.model``), and reply with the answer plus a deterministic
   ``來源`` footer (source_file / doc_id).

Access control (minimal guardrail, spec §5.9): if the environment variable
``BOT_ALLOWLIST`` is unset or empty, everyone is allowed; otherwise it is a
comma-separated list of ids matched against the chat_id OR user_id.

Connection: ``chromadb.CloudClient`` with credentials from
``CHROMA_API_KEY / CHROMA_TENANT / CHROMA_DATABASE``; Groq uses
``GROQ_API_KEY``; Telegram uses ``TELEGRAM_BOT_TOKEN``.

The pure-logic core (``get_active_kb_run_id`` / ``retrieve_chunks`` /
``is_allowed`` / ``build_prompt`` / ``answer_question``) takes injected
collections/embed/chat callables, so it is fully testable against a local
ephemeral ChromaDB with no Telegram/Groq network calls.
"""
from __future__ import annotations

import os
import pathlib
import sys
from typing import Any, Callable

import chromadb
import yaml

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "pipeline.yaml"

EmbedFn = Callable[[list[str]], list[list[float]]]
ChatFn = Callable[[str, str], str]  # (prompt, model) -> reply text


def load_config(path: pathlib.Path = CONFIG_PATH) -> dict[str, Any]:
    """Load pipeline configuration from YAML."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def connect_chroma() -> chromadb.CloudClient:
    """Connect to Chroma Cloud using credentials from environment variables."""
    return chromadb.CloudClient(
        api_key=os.environ["CHROMA_API_KEY"],
        tenant=os.environ["CHROMA_TENANT"],
        database=os.environ["CHROMA_DATABASE"],
    )


def get_active_kb_run_id(control_collection: Any, pointer_id: str) -> str:
    """Read the published kb_run_id from the control collection."""
    record = control_collection.get(ids=[pointer_id], include=["metadatas"])
    if not record["ids"]:
        raise RuntimeError(
            f"active pointer '{pointer_id}' not found in control collection"
        )
    return record["metadatas"][0]["active_kb_run_id"]


def retrieve_chunks(
    data_collection: Any,
    active_kb_run_id: str,
    query_vector: list[float],
    top_k: int,
) -> list[dict[str, Any]]:
    """Retrieve top_k chunks of the active version only (spec §5.9 step 2)."""
    result = data_collection.query(
        query_embeddings=[query_vector],
        n_results=top_k,
        where={"kb_run_id": active_kb_run_id},
        include=["documents", "metadatas"],
    )
    return [
        {"text": doc, "metadata": meta}
        for doc, meta in zip(result["documents"][0], result["metadatas"][0])
    ]


def is_allowed(chat_id: int | None, user_id: int | None) -> bool:
    """Allowlist check: unset/empty BOT_ALLOWLIST allows everyone."""
    raw = os.environ.get("BOT_ALLOWLIST")
    if raw is None or not raw.strip():
        return True
    allowed = {int(item) for item in raw.split(",") if item.strip()}
    return chat_id in allowed or user_id in allowed


def build_prompt(question: str, chunks: list[dict[str, Any]]) -> str:
    """Assemble the Groq prompt: grounded instructions + sources + question."""
    blocks = []
    for i, chunk in enumerate(chunks, start=1):
        meta = chunk["metadata"]
        blocks.append(
            f"[{i}] 來源：{meta.get('source_file', '未知')}"
            f"（doc_id: {meta.get('doc_id', '未知')}）\n{chunk['text']}"
        )
    context = "\n\n".join(blocks)
    return (
        "你是企業內部知識庫助理。請只根據下列參考資料以繁體中文回答問題；"
        "若參考資料不足以回答，請明確說明無法回答，不要編造內容。\n\n"
        f"參考資料：\n{context}\n\n"
        f"問題：{question}"
    )


def format_sources(chunks: list[dict[str, Any]]) -> str:
    """Deterministic source footer (source_file / doc_id) appended to the reply."""
    lines = [
        f"- {chunk['metadata'].get('source_file', '未知')}"
        f"（doc_id: {chunk['metadata'].get('doc_id', '未知')}）"
        for chunk in chunks
    ]
    return "來源：\n" + "\n".join(lines)


def answer_question(
    question: str,
    *,
    config: dict[str, Any],
    control_collection: Any,
    data_collection: Any,
    embed_fn: EmbedFn,
    chat_fn: ChatFn,
) -> str:
    """Full read-only RAG answer for one question (spec §5.9 steps 1-3)."""
    vectorstore = config.get("vectorstore", {})
    pointer_id = vectorstore.get("control_pointer_id", "active_pointer")
    top_k = int(config.get("retrieval", {}).get("top_k", 5))
    model = config.get("llm", {}).get("model", "llama-3.1-8b-instant")

    active_kb_run_id = get_active_kb_run_id(control_collection, pointer_id)
    query_vector = embed_fn([question])[0]
    chunks = retrieve_chunks(data_collection, active_kb_run_id, query_vector, top_k)
    if not chunks:
        return "知識庫中查無相關資料。"
    reply = chat_fn(build_prompt(question, chunks), model)
    return f"{reply}\n\n{format_sources(chunks)}"


def _default_embed_fn(config: dict[str, Any]) -> EmbedFn:
    """Embed user questions with the pipeline's sentence-transformers model."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(config["embedding"]["model"])
    return lambda texts: model.encode(texts).tolist()


def _default_chat_fn() -> ChatFn:
    """Call Groq chat completions (GROQ_API_KEY from the environment)."""
    from groq import Groq

    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    def chat(prompt: str, model: str) -> str:
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        return completion.choices[0].message.content

    return chat


def main() -> None:
    from telegram.ext import Application, MessageHandler, filters

    config = load_config()
    vectorstore = config.get("vectorstore", {})
    client = connect_chroma()
    control_collection = client.get_collection(
        vectorstore.get("control_collection_name", "company_kb_control")
    )
    data_collection = client.get_collection(
        vectorstore.get("data_collection_name", "company_kb_data")
    )
    embed_fn = _default_embed_fn(config)
    chat_fn = _default_chat_fn()

    async def on_message(update, context) -> None:
        if not is_allowed(
            update.effective_chat.id,
            update.effective_user.id if update.effective_user else None,
        ):
            return
        try:
            reply = answer_question(
                update.message.text,
                config=config,
                control_collection=control_collection,
                data_collection=data_collection,
                embed_fn=embed_fn,
                chat_fn=chat_fn,
            )
        except Exception as exc:  # noqa: BLE001 — never leak internals to users
            print(f"bot query failed: {exc}", file=sys.stderr)
            reply = "查詢時發生錯誤，請稍後再試。"
        await update.message.reply_text(reply)

    application = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    application.run_polling()


if __name__ == "__main__":
    main()

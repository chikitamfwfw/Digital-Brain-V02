from __future__ import annotations
import logging

import chromadb
from chromadb.utils import embedding_functions

import config

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "second_brain"
_EMBED_MODEL = "paraphrase-multilingual-mpnet-base-v2"


class KnowledgeStore:
    def __init__(self):
        logger.info(f"Initializing ChromaDB at {config.CHROMA_DB_PATH}")
        logger.info(f"Loading embedding model: {_EMBED_MODEL} (~400MB on first run)")

        self._client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
        self._ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=_EMBED_MODEL
        )
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        return self._collection.count()

    def add_note(self, note_id: str, content: str, metadata: dict | None = None):
        """ノートをベクトル化して保存。既存IDは上書き。"""
        if metadata is None:
            metadata = {}
        # 既存エントリを削除（upsert的な動作）
        try:
            self._collection.delete(ids=[note_id])
        except Exception:
            pass

        self._collection.add(
            ids=[note_id],
            documents=[content],
            metadatas=[metadata],
        )
        logger.debug(f"KnowledgeStore: added note {note_id}")

    def search(self, query: str, n_results: int = 5) -> list[dict]:
        """
        セマンティック検索。上位 n_results 件を返す。
        Returns: [{"id": str, "content": str, "metadata": dict, "distance": float}]
        """
        total = self._collection.count()
        if total == 0:
            return []

        actual_n = min(n_results, total)
        results = self._collection.query(
            query_texts=[query],
            n_results=actual_n,
            include=["documents", "metadatas", "distances"],
        )

        notes = []
        for i, doc_id in enumerate(results["ids"][0]):
            notes.append({
                "id": doc_id,
                "content": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            })
        return notes

    def delete_note(self, note_id: str):
        try:
            self._collection.delete(ids=[note_id])
        except Exception:
            pass

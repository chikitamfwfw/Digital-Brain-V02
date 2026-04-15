from __future__ import annotations
import logging
from typing import AsyncGenerator

import anthropic

import config

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _build_system(command: str) -> list[dict]:
    """
    システムプロンプトを組み立てる。
    先頭ブロックにプロンプトキャッシュを適用してコスト削減。
    """
    system_prompt = config.get_system_prompt()
    command_prompt = config.get_command_prompt(command)

    combined = system_prompt
    if command_prompt:
        combined += f"\n\n---\n\n{command_prompt}"

    return [
        {
            "type": "text",
            "text": combined,
            "cache_control": {"type": "ephemeral"},
        }
    ]


async def chat(
    command: str,
    history: list[dict],
    user_message: str,
    context_notes: list[dict] | None = None,
) -> str:
    """
    メイン会話（claude-sonnet-4-6）。

    context_notes: ChromaDB から取得した関連ノートのリスト
      [{"id": "ZK-...", "content": "..."}]
    """
    client = _get_client()
    system = _build_system(command)

    messages = list(history)

    # 関連ノートをユーザーメッセージの前に注入
    if context_notes:
        notes_text = "\n\n".join(
            f"### 参照ノート: {n['id']}\n{n['content']}" for n in context_notes
        )
        injected = f"【蓄積知識（関連ノート）】\n{notes_text}\n\n---\n\n{user_message}"
    else:
        injected = user_message

    messages.append({"role": "user", "content": injected})

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=messages,
    )

    return response.content[0].text


async def classify(text: str, prompt: str) -> str:
    """
    軽量分類・タグ付け（claude-haiku-4-5）。
    prompt にタスク指示を含める。
    """
    client = _get_client()

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": f"{prompt}\n\n{text}"}],
    )

    return response.content[0].text.strip()


async def extract_tags(text: str) -> list[str]:
    """テキストからタグを抽出（Haiku使用）"""
    result = await classify(
        text,
        "以下のテキストから関連するタグを5個以内で抽出してください。"
        "カンマ区切りで、#なしで返してください。例: AI, 生産性, Python",
    )
    return [t.strip().lstrip("#") for t in result.split(",") if t.strip()]


async def extract_permanent_idea(session_history: list[dict]) -> dict:
    """
    セッション履歴から原子的アイデアを抽出してPermanent Note用データを返す。
    Returns: {"idea": str, "elaboration": str, "tags": list[str]}
    """
    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in session_history
    )

    prompt = (
        "以下の会話から、最も重要な原子的アイデア（一つのコアコンセプト）を抽出してください。\n\n"
        "出力形式:\n"
        "IDEA: （一文でアイデアのタイトル）\n"
        "ELABORATION: （3〜5文でアイデアの詳細説明）\n"
        "TAGS: （カンマ区切りでタグ5個以内）\n\n"
        f"会話:\n{history_text}"
    )

    result = await classify("", prompt)
    lines = result.strip().splitlines()
    idea = elaboration = ""
    tags = []

    for line in lines:
        if line.startswith("IDEA:"):
            idea = line.removeprefix("IDEA:").strip()
        elif line.startswith("ELABORATION:"):
            elaboration = line.removeprefix("ELABORATION:").strip()
        elif line.startswith("TAGS:"):
            tags = [t.strip() for t in line.removeprefix("TAGS:").split(",") if t.strip()]

    return {"idea": idea, "elaboration": elaboration, "tags": tags}


async def translate_to_japanese(text: str) -> str:
    """英語テキストを日本語に翻訳（Sonnet使用）"""
    client = _get_client()
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8096,
        messages=[
            {
                "role": "user",
                "content": (
                    "以下の英語テキストを自然な日本語に翻訳してください。"
                    "専門用語はカタカナ表記のまま残してください。\n\n"
                    f"{text}"
                ),
            }
        ],
    )
    return response.content[0].text.strip()

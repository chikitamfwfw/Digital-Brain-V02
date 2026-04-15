from __future__ import annotations
import json
import logging
import re
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
    Returns: {"title": str, "thesis": str, "elaboration": str,
              "significance": str, "application": str, "limitations": str, "tags": list[str]}
    """
    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content'][:500]}" for m in session_history
    )

    prompt = (
        "以下の会話から、最も重要な原子的アイデア（一つのコアコンセプト）を抽出し、"
        "Permanent Noteとして構造化してください。\n\n"
        "必ず以下のJSON形式のみを返してください。\n\n"
        "```json\n"
        "{\n"
        '  "title": "アイデアを10〜20字で表したタイトル",\n'
        '  "thesis": "アイデアの核心を1〜2文で。何を主張しているか",\n'
        '  "elaboration": "アイデアの詳細説明を3〜5文で",\n'
        '  "significance": "なぜこのアイデアが重要か・どんな価値があるか",\n'
        '  "application": "映像・企画・音楽・IPコンテンツへの接続・応用可能性。関連薄い場合は空文字",\n'
        '  "limitations": "このアイデアの限界・反証可能性・未検証の前提",\n'
        '  "tags": ["タグ1", "タグ2"]\n'
        "}\n"
        "```\n\n"
        f"会話:\n{history_text}"
    )

    client = _get_client()
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    result = response.content[0].text.strip()
    parsed = parse_json_response(result)

    return {
        "title": parsed.get("title", ""),
        "thesis": parsed.get("thesis", ""),
        "elaboration": parsed.get("elaboration", ""),
        "significance": parsed.get("significance", ""),
        "application": parsed.get("application", ""),
        "limitations": parsed.get("limitations", ""),
        "tags": parsed.get("tags", []),
    }


async def generate_draft(command: str, history: list[dict]) -> tuple[dict, str]:
    """
    セッション履歴からドラフトをJSON形式で生成。
    保存トリガーをシステムとして送信し、構造化出力を得る。
    Returns: (parsed_dict, raw_text)
    """
    client = _get_client()
    system = _build_system(command)

    messages = list(history)
    messages.append({
        "role": "user",
        "content": "[保存リクエスト] これまでのセッション内容を整理して、指定のJSON形式でドラフトを作成してください。",
    })

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=messages,
    )
    raw = response.content[0].text.strip()
    return parse_json_response(raw), raw


def parse_json_response(text: str) -> dict:
    """
    Claudeのレスポンスから JSON を抽出してパース。
    ```json ... ``` ブロック → 生JSON の順で探す。失敗時は空dict。
    """
    # ```json ... ``` ブロック
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # コードブロックなしの生JSON
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}


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

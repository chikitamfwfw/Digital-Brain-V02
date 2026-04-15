from __future__ import annotations
import logging

import discord

from services.knowledge_store import KnowledgeStore
from utils.formatters import truncate_for_discord
import config

logger = logging.getLogger(__name__)

_GITHUB_BASE = f"https://github.com/{config.GITHUB_REPO}/blob/main"

_TYPE_EMOJI = {
    "fleeting": "💭",
    "article": "📄",
    "youtube": "🎬",
    "permanent": "🌟",
    "research": "🔍",
    "planning": "🗂️",
}


class SearchHandler:
    def __init__(self, store: KnowledgeStore):
        self.store = store

    async def handle(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True)

        try:
            results = self.store.search(query, n_results=5)

            if not results:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title=f"🔎 「{query}」",
                        description="一致するノートが見つかりませんでした。",
                        color=0x99AAB5,
                    )
                )
                return

            embed = discord.Embed(
                title=f"🔎 「{query}」の検索結果",
                description=f"{len(results)}件のノートが見つかりました",
                color=0x5865F2,
            )

            for r in results:
                note_id = r["id"]
                metadata = r.get("metadata", {})
                note_type = metadata.get("type", "")
                note_path = metadata.get("path", "")
                note_date = metadata.get("date", "")

                emoji = _TYPE_EMOJI.get(note_type, "📝")
                snippet = r.get("content", "")[:150].replace("\n", " ")

                # GitHub リンク
                github_link = f"[GitHub で開く]({_GITHUB_BASE}/{note_path})" if note_path else ""

                field_value = f"`{note_date}` {snippet}…\n{github_link}"
                embed.add_field(
                    name=f"{emoji} {note_id}",
                    value=truncate_for_discord(field_value, 300),
                    inline=False,
                )

            embed.set_footer(text=f"セマンティック検索 | ChromaDB")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.exception(f"SearchHandler error: {e}")
            await interaction.followup.send(f"⚠️ 検索エラー: {e}")

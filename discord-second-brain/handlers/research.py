from __future__ import annotations
import logging
from datetime import datetime, timezone

import discord

from session.manager import SessionManager
from services.github_client import GitHubClient
from services.knowledge_store import KnowledgeStore
from services import claude_client, tavily_client
from services.claude_client import parse_json_response
from utils.formatters import (
    generate_zk_id,
    render_research_note,
    render_permanent_note,
    truncate_for_discord,
    today_str,
)
import config

logger = logging.getLogger(__name__)


class ResearchHandler:
    def __init__(self, sessions: SessionManager, github: GitHubClient, store: KnowledgeStore):
        self.sessions = sessions
        self.github = github
        self.store = store

    # ─── /research 初回 ────────────────────────────────────────────────────────

    async def handle(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True)
        channel_id = interaction.channel_id
        session = self.sessions.get_or_create(channel_id, "research")
        session._research_query = query
        session._research_mode = "chat"
        session._research_sources = []
        session._research_draft_data = {}
        session._research_tags = []

        try:
            # 1. Tavily Web検索
            tavily_results, tavily_answer = await tavily_client.search(query, max_results=5)
            session._research_sources = tavily_results

            # 2. ChromaDB 関連ノート検索
            related = self.store.search(query, n_results=3)
            for r in related:
                self.sessions.add_reference(channel_id, r["id"])

            # 3. 検索結果をプロンプトに組み込み
            sources_text = tavily_client.format_results(tavily_results, tavily_answer, query)
            user_message = f"{sources_text}\n\nリサーチクエリ: {query}\n上記の検索結果と蓄積ノートを参照して整理してください。"

            # 4. Claude で合成
            response_text = await claude_client.chat(
                command="research",
                history=session.history,
                user_message=user_message,
                context_notes=related if related else None,
            )
            self.sessions.add_message(channel_id, "user", user_message)
            self.sessions.add_message(channel_id, "assistant", response_text)

            # 5. Discord に表示
            embed = discord.Embed(
                title=f"🔍 {query}",
                description=truncate_for_discord(response_text),
                color=0x5865F2,
            )
            embed.set_footer(text=f"Web検索: {len(tavily_results)}件 | 過去ノート: {len(related)}件 | チャンネルにメッセージを送って深掘りできます")
            if related:
                embed.add_field(name="参照ノート", value="\n".join(f"• {r['id']}" for r in related), inline=False)

            view = ResearchChatView(self, channel_id)
            await interaction.followup.send(embed=embed, view=view)

        except Exception as e:
            logger.exception(f"ResearchHandler.handle error: {e}")
            await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

    # ─── チャット継続（on_message から呼ばれる） ──────────────────────────────

    async def continue_chat(self, message: discord.Message):
        channel_id = message.channel.id
        session = self.sessions.get(channel_id)
        if not session or not session.active:
            return

        # draft_review モード中に通常メッセージ → 修正扱い
        if getattr(session, "_research_mode", "chat") == "draft_review":
            await self._handle_modification(channel_id, message.content, message.channel)
            return

        async with message.channel.typing():
            related = self.store.search(message.content, n_results=3)
            response_text = await claude_client.chat(
                command="research",
                history=session.history,
                user_message=message.content,
                context_notes=related if related else None,
            )

        self.sessions.add_message(channel_id, "user", message.content)
        self.sessions.add_message(channel_id, "assistant", response_text)

        embed = discord.Embed(
            description=truncate_for_discord(response_text),
            color=0x5865F2,
        )
        view = ResearchChatView(self, channel_id)
        await message.channel.send(embed=embed, view=view)

    # ─── [💾 ドラフト作成] ────────────────────────────────────────────────────

    async def create_draft(self, channel_id: int, interaction: discord.Interaction):
        session = self.sessions.get(channel_id)
        if not session:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            parsed, _ = await claude_client.generate_draft("research", session.history)

            query = getattr(session, "_research_query", "")
            tags = await claude_client.extract_tags(query + "\n" + parsed.get("summary", ""))
            session._research_draft_data = parsed
            session._research_tags = tags
            session._research_mode = "draft_review"

            await self._send_draft_preview(parsed, tags, interaction.followup.send)

        except Exception as e:
            logger.exception(f"ResearchHandler.create_draft error: {e}")
            await interaction.followup.send(f"⚠️ ドラフト生成エラー: {e}")

    async def _send_draft_preview(self, parsed: dict, tags: list, send_fn):
        title = parsed.get("title", "リサーチノート")
        summary = parsed.get("summary", "")
        key_findings = parsed.get("key_findings", "")

        embed = discord.Embed(
            title=f"📋 ドラフト: {title}",
            description=truncate_for_discord(summary),
            color=0xFEE75C,
        )
        if key_findings:
            embed.add_field(name="主要な発見", value=truncate_for_discord(key_findings, 600), inline=False)
        if tags:
            embed.add_field(name="タグ", value=" ".join(f"`#{t}`" for t in tags), inline=False)
        embed.set_footer(text="承認すると GitHub に保存されます")

        view = ResearchReviewView(self, None)  # channel_id はボタンから取得
        return await send_fn(embed=embed, view=view)

    # ─── [✅ 保存] ─────────────────────────────────────────────────────────────

    async def save(self, channel_id: int, interaction: discord.Interaction):
        session = self.sessions.get(channel_id)
        if not session:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            parsed = getattr(session, "_research_draft_data", {})
            tags = getattr(session, "_research_tags", [])
            query = getattr(session, "_research_query", "")
            zk_id = generate_zk_id()

            template = self.github.read_file("_templates/research-session.md")
            note_content = render_research_note(
                zk_id=zk_id,
                query=query,
                title=parsed.get("title", query),
                summary=parsed.get("summary", ""),
                key_findings=parsed.get("key_findings", ""),
                sources=parsed.get("sources", ""),
                insights=parsed.get("insights", ""),
                open_questions=parsed.get("open_questions", ""),
                tags=tags,
                references=session.references,
                template=template,
            )

            month = datetime.now(timezone.utc).strftime("%Y-%m")
            note_path = f"20-research/{month}/{zk_id}.md"
            url = self.github.save_file(
                note_path,
                note_content,
                self.github.build_commit_msg("research", zk_id),
            )
            self.store.add_note(
                note_id=zk_id,
                content=note_content,
                metadata={"type": "research", "path": note_path, "date": today_str()},
            )
            session.saved_path = note_path
            self.sessions.end(channel_id)

            embed = discord.Embed(
                title="✅ 保存しました",
                description=f"`{note_path}`",
                color=0x57F287,
                url=url,
            )
            view = PermanentView(self, channel_id, session)
            await interaction.followup.send(embed=embed, view=view)

        except Exception as e:
            logger.exception(f"ResearchHandler.save error: {e}")
            await interaction.followup.send(f"⚠️ 保存エラー: {e}")

    # ─── [🔄 修正] ─────────────────────────────────────────────────────────────

    async def open_modify_modal(self, channel_id: int, interaction: discord.Interaction):
        modal = ModifyModal(self, channel_id)
        await interaction.response.send_modal(modal)

    async def _handle_modification(self, channel_id: int, modification: str, channel):
        """修正内容を受けてドラフトを再生成"""
        session = self.sessions.get(channel_id)
        if not session:
            return

        async with channel.typing():
            # 修正指示をヒストリーに追加してドラフト再生成
            mod_message = f"[修正指示] {modification}"
            self.sessions.add_message(channel_id, "user", mod_message)

            parsed, _ = await claude_client.generate_draft("research", session.history)
            query = getattr(session, "_research_query", "")
            tags = await claude_client.extract_tags(query + "\n" + parsed.get("summary", ""))

            session._research_draft_data = parsed
            session._research_tags = tags
            session._research_mode = "draft_review"

        embed = discord.Embed(
            title=f"📋 ドラフト更新: {parsed.get('title', '')}",
            description=truncate_for_discord(parsed.get("summary", "")),
            color=0xFEE75C,
        )
        key_findings = parsed.get("key_findings", "")
        if key_findings:
            embed.add_field(name="主要な発見", value=truncate_for_discord(key_findings, 600), inline=False)
        if tags:
            embed.add_field(name="タグ", value=" ".join(f"`#{t}`" for t in tags), inline=False)
        embed.set_footer(text="承認すると GitHub に保存されます")

        view = ResearchReviewView(self, channel_id)
        await channel.send(embed=embed, view=view)

    # ─── [❌ 終了] ─────────────────────────────────────────────────────────────

    async def discard(self, channel_id: int, interaction: discord.Interaction):
        self.sessions.delete(channel_id)
        await interaction.response.send_message("🗑️ セッションを終了しました。", ephemeral=True)

    # ─── Permanent化 ──────────────────────────────────────────────────────────

    async def permanentize(self, channel_id: int, session, interaction: discord.Interaction):
        from handlers.memo import MemoHandler
        memo = MemoHandler(self.sessions, self.github, self.store)
        await memo.permanentize(channel_id, session, interaction)


# ─── Discord UI ────────────────────────────────────────────────────────────────

class ResearchChatView(discord.ui.View):
    def __init__(self, handler: ResearchHandler, channel_id: int):
        super().__init__(timeout=3600)
        self.handler = handler
        self.channel_id = channel_id

    @discord.ui.button(label="💾 ドラフト作成", style=discord.ButtonStyle.primary)
    async def draft_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.handler.create_draft(self.channel_id, interaction)

    @discord.ui.button(label="❌ 終了", style=discord.ButtonStyle.danger)
    async def discard_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.handler.discard(self.channel_id, interaction)


class ResearchReviewView(discord.ui.View):
    def __init__(self, handler: ResearchHandler, channel_id: int):
        super().__init__(timeout=3600)
        self.handler = handler
        self.channel_id = channel_id

    @discord.ui.button(label="✅ 保存", style=discord.ButtonStyle.success)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        channel_id = self.channel_id or interaction.channel_id
        await self.handler.save(channel_id, interaction)

    @discord.ui.button(label="🔄 修正", style=discord.ButtonStyle.secondary)
    async def modify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel_id = self.channel_id or interaction.channel_id
        await self.handler.open_modify_modal(channel_id, interaction)

    @discord.ui.button(label="❌ 破棄", style=discord.ButtonStyle.danger)
    async def discard_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        channel_id = self.channel_id or interaction.channel_id
        await self.handler.discard(channel_id, interaction)


class ModifyModal(discord.ui.Modal, title="ドラフトの修正指示"):
    modification = discord.ui.TextInput(
        label="修正内容",
        style=discord.TextStyle.paragraph,
        placeholder="どのように修正しますか？（例: 結論をもっと実践的に / 映像制作への応用を追加）",
        max_length=500,
    )

    def __init__(self, handler: ResearchHandler, channel_id: int):
        super().__init__()
        self.handler = handler
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        session = self.handler.sessions.get(self.channel_id)
        if not session:
            await interaction.followup.send("セッションが見つかりません。", ephemeral=True)
            return
        await self.handler._handle_modification(self.channel_id, str(self.modification), interaction.channel)
        # followup already sent inside _handle_modification via channel.send


class PermanentView(discord.ui.View):
    def __init__(self, handler: ResearchHandler, channel_id: int, session):
        super().__init__(timeout=1800)
        self.handler = handler
        self.channel_id = channel_id
        self.session = session

    @discord.ui.button(label="🌟 Permanent化", style=discord.ButtonStyle.secondary)
    async def permanent_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.handler.permanentize(self.channel_id, self.session, interaction)

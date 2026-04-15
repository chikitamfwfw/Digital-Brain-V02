from __future__ import annotations
import logging
from datetime import datetime, timezone

import discord

from session.manager import SessionManager
from services.github_client import GitHubClient
from services.knowledge_store import KnowledgeStore
from services import claude_client
from utils.formatters import (
    generate_zk_id,
    render_planning_note,
    truncate_for_discord,
    today_str,
)
import config

logger = logging.getLogger(__name__)


class PlanningHandler:
    def __init__(self, sessions: SessionManager, github: GitHubClient, store: KnowledgeStore):
        self.sessions = sessions
        self.github = github
        self.store = store

    # ─── /planning 初回 ────────────────────────────────────────────────────────

    async def handle(self, interaction: discord.Interaction, topic: str):
        await interaction.response.defer(thinking=True)
        channel_id = interaction.channel_id
        session = self.sessions.get_or_create(channel_id, "planning")
        session._planning_topic = topic
        session._planning_mode = "chat"
        session._planning_draft_data = {}
        session._planning_tags = []

        try:
            # ChromaDB 関連ノート検索
            related = self.store.search(topic, n_results=3)
            for r in related:
                self.sessions.add_reference(channel_id, r["id"])

            response_text = await claude_client.chat(
                command="planning",
                history=session.history,
                user_message=f"企画テーマ: {topic}",
                context_notes=related if related else None,
            )
            self.sessions.add_message(channel_id, "user", f"企画テーマ: {topic}")
            self.sessions.add_message(channel_id, "assistant", response_text)

            embed = discord.Embed(
                title=f"🗂️ {topic}",
                description=truncate_for_discord(response_text),
                color=0x57F287,
            )
            embed.set_footer(text="チャンネルにメッセージを送って壁打ちを続けられます")
            if related:
                embed.add_field(name="参照ノート", value="\n".join(f"• {r['id']}" for r in related), inline=False)

            view = PlanningChatView(self, channel_id)
            await interaction.followup.send(embed=embed, view=view)

        except Exception as e:
            logger.exception(f"PlanningHandler.handle error: {e}")
            await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

    # ─── チャット継続 ──────────────────────────────────────────────────────────

    async def continue_chat(self, message: discord.Message):
        channel_id = message.channel.id
        session = self.sessions.get(channel_id)
        if not session or not session.active:
            return

        if getattr(session, "_planning_mode", "chat") == "draft_review":
            await self._handle_modification(channel_id, message.content, message.channel)
            return

        async with message.channel.typing():
            related = self.store.search(message.content, n_results=3)
            response_text = await claude_client.chat(
                command="planning",
                history=session.history,
                user_message=message.content,
                context_notes=related if related else None,
            )

        self.sessions.add_message(channel_id, "user", message.content)
        self.sessions.add_message(channel_id, "assistant", response_text)

        embed = discord.Embed(
            description=truncate_for_discord(response_text),
            color=0x57F287,
        )
        view = PlanningChatView(self, channel_id)
        await message.channel.send(embed=embed, view=view)

    # ─── [💾 ドラフト作成] ────────────────────────────────────────────────────

    async def create_draft(self, channel_id: int, interaction: discord.Interaction):
        session = self.sessions.get(channel_id)
        if not session:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            parsed, _ = await claude_client.generate_draft("planning", session.history)

            topic = getattr(session, "_planning_topic", "")
            tags = await claude_client.extract_tags(topic + "\n" + parsed.get("situation", ""))
            session._planning_draft_data = parsed
            session._planning_tags = tags
            session._planning_mode = "draft_review"

            title = parsed.get("title", topic)
            situation = parsed.get("situation", "")
            ideas = parsed.get("ideas", "")

            embed = discord.Embed(
                title=f"📋 ドラフト: {title}",
                description=truncate_for_discord(situation),
                color=0xFEE75C,
            )
            if ideas:
                embed.add_field(name="アイデア・提案", value=truncate_for_discord(ideas, 600), inline=False)
            if tags:
                embed.add_field(name="タグ", value=" ".join(f"`#{t}`" for t in tags), inline=False)
            embed.set_footer(text="承認すると GitHub に保存されます")

            view = PlanningReviewView(self, channel_id)
            await interaction.followup.send(embed=embed, view=view)

        except Exception as e:
            logger.exception(f"PlanningHandler.create_draft error: {e}")
            await interaction.followup.send(f"⚠️ ドラフト生成エラー: {e}")

    # ─── [✅ 保存] ─────────────────────────────────────────────────────────────

    async def save(self, channel_id: int, interaction: discord.Interaction):
        session = self.sessions.get(channel_id)
        if not session:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            parsed = getattr(session, "_planning_draft_data", {})
            tags = getattr(session, "_planning_tags", [])
            topic = getattr(session, "_planning_topic", "")
            zk_id = generate_zk_id()

            template = self.github.read_file("_templates/planning-session.md")
            note_content = render_planning_note(
                zk_id=zk_id,
                topic=topic,
                title=parsed.get("title", topic),
                situation=parsed.get("situation", ""),
                ideas=parsed.get("ideas", ""),
                action_steps=parsed.get("action_steps", ""),
                concerns=parsed.get("concerns", ""),
                next_actions=parsed.get("next_actions", ""),
                tags=tags,
                references=session.references,
                template=template,
            )

            month = datetime.now(timezone.utc).strftime("%Y-%m")
            note_path = f"30-planning/{month}/{zk_id}.md"
            url = self.github.save_file(
                note_path,
                note_content,
                self.github.build_commit_msg("planning", zk_id),
            )
            self.store.add_note(
                note_id=zk_id,
                content=note_content,
                metadata={"type": "planning", "path": note_path, "date": today_str()},
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
            logger.exception(f"PlanningHandler.save error: {e}")
            await interaction.followup.send(f"⚠️ 保存エラー: {e}")

    # ─── [🔄 修正] ─────────────────────────────────────────────────────────────

    async def open_modify_modal(self, channel_id: int, interaction: discord.Interaction):
        modal = ModifyModal(self, channel_id)
        await interaction.response.send_modal(modal)

    async def _handle_modification(self, channel_id: int, modification: str, channel):
        session = self.sessions.get(channel_id)
        if not session:
            return

        async with channel.typing():
            mod_message = f"[修正指示] {modification}"
            self.sessions.add_message(channel_id, "user", mod_message)

            parsed, _ = await claude_client.generate_draft("planning", session.history)
            topic = getattr(session, "_planning_topic", "")
            tags = await claude_client.extract_tags(topic + "\n" + parsed.get("situation", ""))

            session._planning_draft_data = parsed
            session._planning_tags = tags
            session._planning_mode = "draft_review"

        embed = discord.Embed(
            title=f"📋 ドラフト更新: {parsed.get('title', '')}",
            description=truncate_for_discord(parsed.get("situation", "")),
            color=0xFEE75C,
        )
        ideas = parsed.get("ideas", "")
        if ideas:
            embed.add_field(name="アイデア・提案", value=truncate_for_discord(ideas, 600), inline=False)
        if tags:
            embed.add_field(name="タグ", value=" ".join(f"`#{t}`" for t in tags), inline=False)
        embed.set_footer(text="承認すると GitHub に保存されます")

        view = PlanningReviewView(self, channel_id)
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

class PlanningChatView(discord.ui.View):
    def __init__(self, handler: PlanningHandler, channel_id: int):
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


class PlanningReviewView(discord.ui.View):
    def __init__(self, handler: PlanningHandler, channel_id: int):
        super().__init__(timeout=3600)
        self.handler = handler
        self.channel_id = channel_id

    @discord.ui.button(label="✅ 保存", style=discord.ButtonStyle.success)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.handler.save(self.channel_id, interaction)

    @discord.ui.button(label="🔄 修正", style=discord.ButtonStyle.secondary)
    async def modify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handler.open_modify_modal(self.channel_id, interaction)

    @discord.ui.button(label="❌ 破棄", style=discord.ButtonStyle.danger)
    async def discard_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.handler.discard(self.channel_id, interaction)


class ModifyModal(discord.ui.Modal, title="ドラフトの修正指示"):
    modification = discord.ui.TextInput(
        label="修正内容",
        style=discord.TextStyle.paragraph,
        placeholder="どのように修正しますか？（例: アクションステップを具体的に / リスクをもっと詳しく）",
        max_length=500,
    )

    def __init__(self, handler: PlanningHandler, channel_id: int):
        super().__init__()
        self.handler = handler
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        await self.handler._handle_modification(self.channel_id, str(self.modification), interaction.channel)


class PermanentView(discord.ui.View):
    def __init__(self, handler: PlanningHandler, channel_id: int, session):
        super().__init__(timeout=1800)
        self.handler = handler
        self.channel_id = channel_id
        self.session = session

    @discord.ui.button(label="🌟 Permanent化", style=discord.ButtonStyle.secondary)
    async def permanent_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.handler.permanentize(self.channel_id, self.session, interaction)

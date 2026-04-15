from __future__ import annotations
import logging
import re

import discord

from session.manager import SessionManager
from services.github_client import GitHubClient
from services.knowledge_store import KnowledgeStore
from services import claude_client
from services.claude_client import parse_json_response
from services.scraper import scrape
from services.youtube_client import fetch_transcript, is_supported_video_url
from utils.formatters import (
    generate_zk_id,
    render_literature_article_note,
    render_literature_youtube_note,
    truncate_for_discord,
    today_str,
)
import config

logger = logging.getLogger(__name__)

_YOUTUBE_PATTERN = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)[A-Za-z0-9_-]{11}"
)


def _is_youtube(url: str) -> bool:
    return bool(_YOUTUBE_PATTERN.search(url))


class LinkHandler:
    def __init__(self, sessions: SessionManager, github: GitHubClient, store: KnowledgeStore):
        self.sessions = sessions
        self.github = github
        self.store = store

    async def handle(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer(thinking=True)

        channel_id = interaction.channel_id
        session = self.sessions.get_or_create(channel_id, "link")
        session.references.append(url)

        try:
            if is_supported_video_url(url):
                await self._handle_youtube(interaction, url, channel_id, session)
            else:
                await self._handle_article(interaction, url, channel_id, session)

        except Exception as e:
            logger.exception(f"LinkHandler error: {e}")
            await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

    # ─── 記事 ──────────────────────────────────────────────────────────────────

    async def _handle_article(self, interaction, url, channel_id, session):
        result = scrape(url)

        if not result.success:
            embed = discord.Embed(
                title="⚠️ 記事を取得できませんでした",
                description=f"`{url}`\n\nタイトル・URLのみ保存しますか？",
                color=0xFEE75C,
            )
            view = ArticleFailView(self, channel_id, url, result.title or url)
            await interaction.followup.send(embed=embed, view=view)
            return

        # ChromaDB 検索
        related = self.store.search(result.content[:500], n_results=3)

        # Claude 要約（JSON出力）
        prompt = f"以下の記事を整理してください。\n\nタイトル: {result.title}\nURL: {url}\n\n{result.content[:6000]}"
        response_text = await claude_client.chat(
            command="link",
            history=session.history,
            user_message=prompt,
            context_notes=related if related else None,
        )
        self.sessions.add_message(channel_id, "user", prompt)
        self.sessions.add_message(channel_id, "assistant", response_text)

        parsed = parse_json_response(response_text)
        summary = parsed.get("summary", response_text)
        key_points = parsed.get("key_points", "")
        details = parsed.get("details", "")
        insights = parsed.get("insights", "")
        personal_application = parsed.get("personal_application", "")
        open_questions = parsed.get("open_questions", "")

        tags = await claude_client.extract_tags(result.content[:1000])

        # セッションにメタデータ保存
        session._link_type = "article"
        session._link_url = url
        session._link_title = result.title
        session._link_summary = summary
        session._link_key_points = key_points
        session._link_details = details
        session._link_insights = insights
        session._link_personal_application = personal_application
        session._link_open_questions = open_questions
        session._link_tags = tags

        embed = discord.Embed(
            title=f"📄 {result.title}",
            description=truncate_for_discord(summary),
            color=0x5865F2,
            url=url,
        )
        if key_points:
            embed.add_field(name="要点", value=truncate_for_discord(key_points, 600), inline=False)
        if insights:
            embed.add_field(name="示唆", value=truncate_for_discord(insights, 400), inline=False)
        if tags:
            embed.add_field(name="タグ", value=" ".join(f"`#{t}`" for t in tags), inline=False)
        if related:
            embed.add_field(name="関連ノート", value="\n".join(f"• {r['id']}" for r in related), inline=False)

        view = LinkActionView(self, channel_id)
        await interaction.followup.send(embed=embed, view=view)

    # ─── YouTube ───────────────────────────────────────────────────────────────

    async def _handle_youtube(self, interaction, url, channel_id, session):
        await interaction.followup.send("⏳ 動画を処理中です…（長い動画は15分以上かかる場合があります）")

        yt_result = await fetch_transcript(url)

        channel = interaction.channel

        if not yt_result.success:
            await channel.send("⚠️ 動画の文字起こしに失敗しました。")
            return

        # ChromaDB 検索
        related = self.store.search(yt_result.transcript[:500], n_results=3)

        # Claude 要約（JSON出力）
        transcript_excerpt = yt_result.transcript[:6000]
        prompt = (
            f"以下の動画の文字起こしを整理してください。\n\n"
            f"タイトル: {yt_result.title}\nURL: {url}\n"
            f"取得方法: {yt_result.method} / 元言語: {yt_result.original_lang}\n\n"
            f"{transcript_excerpt}"
        )
        response_text = await claude_client.chat(
            command="link",
            history=session.history,
            user_message=prompt,
            context_notes=related if related else None,
        )
        self.sessions.add_message(channel_id, "user", prompt)
        self.sessions.add_message(channel_id, "assistant", response_text)

        parsed = parse_json_response(response_text)
        summary = parsed.get("summary", response_text)
        key_points = parsed.get("key_points", "")
        details = parsed.get("details", "")
        insights = parsed.get("insights", "")
        personal_application = parsed.get("personal_application", "")
        open_questions = parsed.get("open_questions", "")

        tags = await claude_client.extract_tags(yt_result.transcript[:1000])

        session._link_type = "youtube"
        session._link_url = url
        session._link_title = yt_result.title
        session._link_summary = summary
        session._link_key_points = key_points
        session._link_details = details
        session._link_insights = insights
        session._link_personal_application = personal_application
        session._link_open_questions = open_questions
        session._link_transcript = yt_result.transcript
        session._link_tags = tags

        embed = discord.Embed(
            title=f"🎬 {yt_result.title}",
            description=truncate_for_discord(summary),
            color=0xED4245,
            url=url,
        )
        embed.set_footer(text=f"取得: {yt_result.method} | 元言語: {yt_result.original_lang}")
        if key_points:
            embed.add_field(name="要点", value=truncate_for_discord(key_points, 600), inline=False)
        if insights:
            embed.add_field(name="示唆", value=truncate_for_discord(insights, 400), inline=False)
        if tags:
            embed.add_field(name="タグ", value=" ".join(f"`#{t}`" for t in tags), inline=False)
        if related:
            embed.add_field(name="関連ノート", value="\n".join(f"• {r['id']}" for r in related), inline=False)

        view = LinkActionView(self, channel_id)
        await channel.send(embed=embed, view=view)

    # ─── 保存処理 ──────────────────────────────────────────────────────────────

    async def save(self, channel_id: int, interaction: discord.Interaction):
        session = self.sessions.get(channel_id)
        if not session:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            link_type = getattr(session, "_link_type", "article")
            zk_id = generate_zk_id()

            if link_type == "youtube":
                template = self.github.read_file("_templates/literature-youtube.md")
                note_content = render_literature_youtube_note(
                    zk_id=zk_id,
                    url=session._link_url,
                    title=session._link_title,
                    transcript_excerpt=getattr(session, "_link_transcript", "")[:2000],
                    summary=getattr(session, "_link_summary", ""),
                    key_points=getattr(session, "_link_key_points", ""),
                    details=getattr(session, "_link_details", ""),
                    insights=getattr(session, "_link_insights", ""),
                    personal_application=getattr(session, "_link_personal_application", ""),
                    open_questions=getattr(session, "_link_open_questions", ""),
                    tags=getattr(session, "_link_tags", []),
                    references=session.references,
                    template=template,
                )
                note_path = f"10-notes/literature/youtube/{zk_id}.md"
            else:
                template = self.github.read_file("_templates/literature-article.md")
                note_content = render_literature_article_note(
                    zk_id=zk_id,
                    url=session._link_url,
                    title=session._link_title,
                    summary=getattr(session, "_link_summary", ""),
                    key_points=getattr(session, "_link_key_points", ""),
                    details=getattr(session, "_link_details", ""),
                    insights=getattr(session, "_link_insights", ""),
                    personal_application=getattr(session, "_link_personal_application", ""),
                    open_questions=getattr(session, "_link_open_questions", ""),
                    tags=getattr(session, "_link_tags", []),
                    references=session.references,
                    template=template,
                )
                note_path = f"10-notes/literature/articles/{zk_id}.md"

            url = self.github.save_file(
                note_path,
                note_content,
                self.github.build_commit_msg("link", zk_id),
            )

            self.store.add_note(
                note_id=zk_id,
                content=note_content,
                metadata={"type": link_type, "path": note_path, "date": today_str()},
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
            logger.exception(f"LinkHandler.save error: {e}")
            await interaction.followup.send(f"⚠️ 保存エラー: {e}")

    async def save_url_only(self, channel_id: int, url: str, title: str, interaction: discord.Interaction):
        """ペイウォール記事: タイトル・URLのみ保存"""
        await interaction.response.defer(thinking=True)

        try:
            zk_id = generate_zk_id()
            session = self.sessions.get(channel_id)
            tags = []
            references = [url] + (session.references if session else [])

            template = self.github.read_file("_templates/literature-article.md")
            note_content = render_literature_article_note(
                zk_id=zk_id,
                url=url,
                title=title or url,
                summary="（取得不可 — タイトル・URLのみ保存）",
                key_points="",
                details="",
                insights="",
                personal_application="",
                open_questions="",
                tags=tags,
                references=references,
                template=template,
            )
            note_path = f"10-notes/literature/articles/{zk_id}.md"
            saved_url = self.github.save_file(
                note_path,
                note_content,
                self.github.build_commit_msg("link", zk_id),
            )

            self.store.add_note(
                note_id=zk_id,
                content=note_content,
                metadata={"type": "article", "path": note_path, "date": today_str()},
            )

            if session:
                self.sessions.end(channel_id)

            await interaction.followup.send(
                embed=discord.Embed(
                    title="✅ URL・タイトルのみ保存しました",
                    description=f"`{note_path}`",
                    color=0x57F287,
                    url=saved_url,
                )
            )

        except Exception as e:
            logger.exception(f"LinkHandler.save_url_only error: {e}")
            await interaction.followup.send(f"⚠️ 保存エラー: {e}")

    async def discard(self, channel_id: int, interaction: discord.Interaction):
        self.sessions.delete(channel_id)
        await interaction.response.send_message("🗑️ 破棄しました。", ephemeral=True)

    async def permanentize(self, channel_id: int, session, interaction: discord.Interaction):
        """[🌟 Permanent化] — memo handler と同じロジックを再利用"""
        from handlers.memo import MemoHandler
        memo = MemoHandler(self.sessions, self.github, self.store)
        await memo.permanentize(channel_id, session, interaction)


# ─── Discord UI ────────────────────────────────────────────────────────────────

class LinkActionView(discord.ui.View):
    def __init__(self, handler: LinkHandler, channel_id: int):
        super().__init__(timeout=600)
        self.handler = handler
        self.channel_id = channel_id

    @discord.ui.button(label="💾 保存", style=discord.ButtonStyle.primary)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.handler.save(self.channel_id, interaction)

    @discord.ui.button(label="❌ 破棄", style=discord.ButtonStyle.danger)
    async def discard_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.handler.discard(self.channel_id, interaction)


class ArticleFailView(discord.ui.View):
    def __init__(self, handler: LinkHandler, channel_id: int, url: str, title: str):
        super().__init__(timeout=300)
        self.handler = handler
        self.channel_id = channel_id
        self.url = url
        self.title = title

    @discord.ui.button(label="✅ タイトル・URLのみ保存", style=discord.ButtonStyle.secondary)
    async def save_url_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.handler.save_url_only(self.channel_id, self.url, self.title, interaction)

    @discord.ui.button(label="❌ スキップ", style=discord.ButtonStyle.danger)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.handler.discard(self.channel_id, interaction)


class PermanentView(discord.ui.View):
    def __init__(self, handler: LinkHandler, channel_id: int, session):
        super().__init__(timeout=1800)
        self.handler = handler
        self.channel_id = channel_id
        self.session = session

    @discord.ui.button(label="🌟 Permanent化", style=discord.ButtonStyle.secondary)
    async def permanent_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.handler.permanentize(self.channel_id, self.session, interaction)

from __future__ import annotations
import logging
from datetime import datetime, timezone

import discord

from session.manager import SessionManager
from services.github_client import GitHubClient
from services.knowledge_store import KnowledgeStore
from services import claude_client
from services.claude_client import parse_json_response
from utils.formatters import (
    generate_zk_id,
    render_fleeting_note,
    render_permanent_note,
    truncate_for_discord,
    today_str,
)
import config

logger = logging.getLogger(__name__)


class MemoHandler:
    def __init__(self, sessions: SessionManager, github: GitHubClient, store: KnowledgeStore):
        self.sessions = sessions
        self.github = github
        self.store = store

    async def handle(self, interaction: discord.Interaction, text: str):
        await interaction.response.defer(thinking=True)

        channel_id = interaction.channel_id
        session = self.sessions.get_or_create(channel_id, "memo")

        try:
            # 1. Inbox に生テキストを即時保存
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            inbox_path = f"00-inbox/{ts}.md"
            self.github.save_file(
                inbox_path,
                f"# Inbox\n\n{text}\n",
                f"[bot] inbox: {ts}",
            )
            session.references.append(inbox_path)

            # 2. ChromaDB でセマンティック検索（top 3）
            related = self.store.search(text, n_results=3)
            for r in related:
                session.add_reference(channel_id, r["id"])

            # 3. Claude で整理（JSON出力）
            response_text = await claude_client.chat(
                command="memo",
                history=session.history,
                user_message=text,
                context_notes=related if related else None,
            )
            self.sessions.add_message(channel_id, "user", text)
            self.sessions.add_message(channel_id, "assistant", response_text)

            # JSON パース
            parsed = parse_json_response(response_text)
            title_ja = parsed.get("title_ja", "")
            interpretation = parsed.get("interpretation", response_text)
            significance = parsed.get("significance", "")
            questions = parsed.get("questions", "")

            # 4. タグ抽出（Haiku）
            tags = await claude_client.extract_tags(text + "\n" + interpretation)

            # セッションにメタデータ保存（保存時に使用）
            session._memo_raw = text
            session._memo_title_ja = title_ja
            session._memo_interpretation = interpretation
            session._memo_significance = significance
            session._memo_questions = questions
            session._memo_tags = tags
            session._memo_inbox_path = inbox_path

            # 5. Discord に返答 + ボタン
            embed = discord.Embed(
                title=f"📝 {title_ja}" if title_ja else "📝 メモを整理しました",
                description=truncate_for_discord(interpretation),
                color=0x5865F2,
            )
            if significance:
                embed.add_field(name="なぜ残すか", value=truncate_for_discord(significance, 500), inline=False)
            if questions:
                embed.add_field(name="未解決の問い", value=truncate_for_discord(questions, 400), inline=False)
            if tags:
                embed.add_field(name="タグ", value=" ".join(f"`#{t}`" for t in tags), inline=False)
            if related:
                ref_text = "\n".join(f"• {r['id']}" for r in related)
                embed.add_field(name="関連ノート", value=ref_text, inline=False)

            view = MemoActionView(self, channel_id)
            await interaction.followup.send(embed=embed, view=view)

        except Exception as e:
            logger.exception(f"MemoHandler error: {e}")
            await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")


    async def save(self, channel_id: int, interaction: discord.Interaction):
        """[💾 保存] ボタン処理"""
        session = self.sessions.get(channel_id)
        if not session:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            zk_id = generate_zk_id()
            template = self.github.read_file("_templates/fleeting-note.md")

            note_content = render_fleeting_note(
                zk_id=zk_id,
                raw_text=getattr(session, "_memo_raw", ""),
                title_ja=getattr(session, "_memo_title_ja", ""),
                interpretation=getattr(session, "_memo_interpretation", ""),
                significance=getattr(session, "_memo_significance", ""),
                questions=getattr(session, "_memo_questions", ""),
                tags=getattr(session, "_memo_tags", []),
                references=session.references,
                template=template,
            )

            note_path = f"10-notes/fleeting/{zk_id}.md"
            url = self.github.save_file(
                note_path,
                note_content,
                self.github.build_commit_msg("memo", zk_id),
            )

            # ChromaDB に追加
            self.store.add_note(
                note_id=zk_id,
                content=note_content,
                metadata={"type": "fleeting", "path": note_path, "date": today_str()},
            )

            # Inbox を削除
            inbox_path = getattr(session, "_memo_inbox_path", None)
            if inbox_path:
                self.github.delete_file(inbox_path, f"[bot] clear inbox: {inbox_path}")

            session.saved_path = note_path
            self.sessions.end(channel_id)

            embed = discord.Embed(
                title="✅ 保存しました",
                description=f"`{note_path}`",
                color=0x57F287,
                url=url,
            )
            # [🌟 Permanent化] ボタンを追加表示
            view = PermanentView(self, channel_id, session)
            await interaction.followup.send(embed=embed, view=view)

        except Exception as e:
            logger.exception(f"MemoHandler.save error: {e}")
            await interaction.followup.send(f"⚠️ 保存エラー: {e}")

    async def discard(self, channel_id: int, interaction: discord.Interaction):
        """[❌ 破棄] ボタン処理"""
        session = self.sessions.get(channel_id)
        inbox_path = getattr(session, "_memo_inbox_path", None) if session else None

        if inbox_path:
            try:
                self.github.delete_file(inbox_path, f"[bot] discard: {inbox_path}")
            except Exception:
                pass

        self.sessions.delete(channel_id)
        await interaction.response.send_message("🗑️ 破棄しました。", ephemeral=True)

    async def permanentize(self, channel_id: int, session, interaction: discord.Interaction):
        """[🌟 Permanent化] ボタン処理"""
        await interaction.response.defer(thinking=True)

        try:
            extracted = await claude_client.extract_permanent_idea(session.history)

            # 既存 Permanent Note のリストを取得してバックリンク候補とする
            existing_notes = self.github.list_files("10-notes/permanent")
            # セマンティック検索でより関連性の高いものを上位に
            search_text = extracted["title"] or extracted["thesis"] or ""
            if search_text:
                related = self.store.search(search_text, n_results=3)
                backlinks = [r["id"] for r in related if r["id"].startswith("ZK-")]
            else:
                backlinks = [
                    p.replace("10-notes/permanent/", "").replace(".md", "")
                    for p in existing_notes[:3]
                ]

            zk_id = generate_zk_id()
            template = self.github.read_file("_templates/permanent-note.md")
            note_content = render_permanent_note(
                zk_id=zk_id,
                title=extracted["title"],
                thesis=extracted["thesis"],
                elaboration=extracted["elaboration"],
                significance=extracted["significance"],
                application=extracted["application"],
                limitations=extracted["limitations"],
                backlinks=backlinks,
                tags=extracted["tags"],
                template=template,
            )

            note_path = f"10-notes/permanent/{zk_id}.md"
            url = self.github.save_file(
                note_path,
                note_content,
                self.github.build_commit_msg("permanent", zk_id),
            )

            self.store.add_note(
                note_id=zk_id,
                content=note_content,
                metadata={"type": "permanent", "path": note_path, "date": today_str()},
            )

            embed = discord.Embed(
                title="🌟 Permanent Note を作成しました",
                description=f"**{extracted['title']}**\n\n{extracted['thesis']}",
                color=0xFEE75C,
                url=url,
            )
            embed.add_field(name="ID", value=f"`{zk_id}`", inline=True)
            if backlinks:
                embed.add_field(name="バックリンク", value="\n".join(f"[[{b}]]" for b in backlinks), inline=False)

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.exception(f"MemoHandler.permanentize error: {e}")
            await interaction.followup.send(f"⚠️ Permanent化エラー: {e}")


# ─── Discord UI ────────────────────────────────────────────────────────────────

class MemoActionView(discord.ui.View):
    def __init__(self, handler: MemoHandler, channel_id: int):
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


class PermanentView(discord.ui.View):
    def __init__(self, handler: MemoHandler, channel_id: int, session):
        super().__init__(timeout=1800)
        self.handler = handler
        self.channel_id = channel_id
        self.session = session

    @discord.ui.button(label="🌟 Permanent化", style=discord.ButtonStyle.secondary)
    async def permanent_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.handler.permanentize(self.channel_id, self.session, interaction)

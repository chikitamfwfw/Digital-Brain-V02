import logging
import discord
from discord import app_commands
from discord.ext import commands

import config
from session.manager import SessionManager
from services.github_client import GitHubClient
from services.knowledge_store import KnowledgeStore
from handlers.memo import MemoHandler
from handlers.link import LinkHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class SecondBrainBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

        self.session_manager = SessionManager()
        self.github = GitHubClient()
        self.knowledge_store = KnowledgeStore()
        self.memo_handler = MemoHandler(self.session_manager, self.github, self.knowledge_store)
        self.link_handler = LinkHandler(self.session_manager, self.github, self.knowledge_store)

    async def setup_hook(self):
        guild = discord.Object(id=config.DISCORD_GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        logger.info(f"Synced {len(synced)} slash commands to guild {config.DISCORD_GUILD_ID}")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info("=" * 40)

        # GitHub接続確認
        try:
            self.github.ping()
            logger.info(f"GitHub: connected to {config.GITHUB_REPO}")
        except Exception as e:
            logger.warning(f"GitHub: connection failed — {e}")

        # ChromaDB確認
        try:
            count = self.knowledge_store.count()
            logger.info(f"ChromaDB: ready ({count} notes indexed)")
        except Exception as e:
            logger.warning(f"ChromaDB: init failed — {e}")

        # sentence-transformers の初回ダウンロード通知
        logger.info("Note: First startup may download ~400MB (sentence-transformers) and ~1.5GB (faster-whisper medium)")
        logger.info("=" * 40)


bot = SecondBrainBot()


# ─── /memo ────────────────────────────────────────────────────────────────────

@bot.tree.command(name="memo", description="メモ・アイデアをキャプチャして整理します")
@app_commands.describe(text="メモしたい内容")
async def memo_command(interaction: discord.Interaction, text: str):
    await bot.memo_handler.handle(interaction, text)


# ─── /link ────────────────────────────────────────────────────────────────────

@bot.tree.command(name="link", description="記事・YouTubeのURLを要約して保存します")
@app_commands.describe(url="記事またはYouTubeのURL")
async def link_command(interaction: discord.Interaction, url: str):
    await bot.link_handler.handle(interaction, url)


# ─── エントリーポイント ────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(config.DISCORD_TOKEN)

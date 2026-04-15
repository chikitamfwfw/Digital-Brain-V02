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
from handlers.research import ResearchHandler
from handlers.planning import PlanningHandler
from handlers.search import SearchHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class SecondBrainBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True   # Message Content Intent（Developer Portalで有効化必要）
        super().__init__(command_prefix="!", intents=intents)

        self.session_manager = SessionManager()
        self.github = GitHubClient()
        self.knowledge_store = KnowledgeStore()
        self.memo_handler = MemoHandler(self.session_manager, self.github, self.knowledge_store)
        self.link_handler = LinkHandler(self.session_manager, self.github, self.knowledge_store)
        self.research_handler = ResearchHandler(self.session_manager, self.github, self.knowledge_store)
        self.planning_handler = PlanningHandler(self.session_manager, self.github, self.knowledge_store)
        self.search_handler = SearchHandler(self.knowledge_store)

    async def setup_hook(self):
        guild = discord.Object(id=config.DISCORD_GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        logger.info(f"Synced {len(synced)} slash commands to guild {config.DISCORD_GUILD_ID}")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info("=" * 40)

        try:
            self.github.ping()
            logger.info(f"GitHub: connected to {config.GITHUB_REPO}")
        except Exception as e:
            logger.warning(f"GitHub: connection failed — {e}")

        try:
            count = self.knowledge_store.count()
            logger.info(f"ChromaDB: ready ({count} notes indexed)")
        except Exception as e:
            logger.warning(f"ChromaDB: init failed — {e}")

        logger.info("=" * 40)

    async def on_message(self, message: discord.Message):
        """チャット継続（/research, /planning のセッション中のみ応答）"""
        if message.author.bot:
            return

        await self.process_commands(message)

        session = self.session_manager.get(message.channel.id)
        if not session or not session.active:
            return

        if session.command == "research":
            await self.research_handler.continue_chat(message)
        elif session.command == "planning":
            await self.planning_handler.continue_chat(message)


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


# ─── /research ────────────────────────────────────────────────────────────────

@bot.tree.command(name="research", description="Webと過去ノートを横断してリサーチします")
@app_commands.describe(query="リサーチしたいテーマやキーワード")
async def research_command(interaction: discord.Interaction, query: str):
    await bot.research_handler.handle(interaction, query)


# ─── /planning ────────────────────────────────────────────────────────────────

@bot.tree.command(name="planning", description="企画の壁打ちをClaudeと行います")
@app_commands.describe(topic="壁打ちしたい企画や課題")
async def planning_command(interaction: discord.Interaction, topic: str):
    await bot.planning_handler.handle(interaction, topic)


# ─── /search ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="search", description="過去ノートをセマンティック検索します")
@app_commands.describe(query="検索キーワード")
async def search_command(interaction: discord.Interaction, query: str):
    await bot.search_handler.handle(interaction, query)


# ─── エントリーポイント ────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(config.DISCORD_TOKEN)

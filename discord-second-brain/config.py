from __future__ import annotations
import os
import time
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")
COOKIES_FILE = os.getenv("COOKIES_FILE")

# _config/ キャッシュ（TTL: 5分）
_config_cache: dict[str, tuple[str, float]] = {}
_CONFIG_TTL = 300

_github_client = None


def _get_github_client():
    global _github_client
    if _github_client is None:
        from services.github_client import GitHubClient
        _github_client = GitHubClient()
    return _github_client


def get_config(path: str) -> str:
    """GitHub _config/ のファイルを取得（TTLキャッシュ付き）"""
    now = time.time()
    if path in _config_cache:
        content, ts = _config_cache[path]
        if now - ts < _CONFIG_TTL:
            return content

    try:
        content = _get_github_client().read_file(path)
    except Exception:
        content = _config_cache.get(path, ("", 0))[0]

    _config_cache[path] = (content, now)
    return content


def get_system_prompt() -> str:
    return get_config("_config/system-prompt.md")


def get_command_prompt(command: str) -> str:
    return get_config(f"_config/prompts/{command}.md")

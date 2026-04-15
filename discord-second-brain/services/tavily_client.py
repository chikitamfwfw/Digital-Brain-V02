from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass

import config

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    score: float


def _do_search(query: str, max_results: int) -> tuple[list[SearchResult], str]:
    from tavily import TavilyClient
    client = TavilyClient(api_key=config.TAVILY_API_KEY)
    response = client.search(
        query=query,
        max_results=max_results,
        search_depth="basic",
    )
    results = [
        SearchResult(
            title=r.get("title", ""),
            url=r.get("url", ""),
            content=r.get("content", ""),
            score=r.get("score", 0.0),
        )
        for r in response.get("results", [])
    ]
    answer = response.get("answer", "")
    return results, answer


async def search(query: str, max_results: int = 5) -> tuple[list[SearchResult], str]:
    """Tavily APIでWeb検索（非同期）。Returns: (results, answer)"""
    loop = asyncio.get_event_loop()
    results, answer = await loop.run_in_executor(None, _do_search, query, max_results)
    logger.info(f"Tavily: {len(results)} results for '{query}'")
    return results, answer


def format_results(results: list[SearchResult], answer: str, query: str) -> str:
    """検索結果をClaudeへ渡すテキストに整形"""
    lines = ["【Web検索結果】", f"クエリ: {query}", ""]
    if answer:
        lines += [f"Tavilyサマリー: {answer}", ""]
    for i, r in enumerate(results, 1):
        lines += [
            f"{i}. {r.title}",
            f"   URL: {r.url}",
            f"   {r.content[:400]}",
            "",
        ]
    return "\n".join(lines)

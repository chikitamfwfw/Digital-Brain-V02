from __future__ import annotations
import http.cookiejar
import logging
import os
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
import trafilatura

import config

logger = logging.getLogger(__name__)

_MAX_PAGES = 8
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


@dataclass
class ScrapeResult:
    success: bool
    title: str
    content: str
    url: str
    pages_fetched: int = 1


def scrape(url: str) -> ScrapeResult:
    """
    記事テキストを取得する。
    cookies.txt が設定されている場合は最初からCookie付きで取得する（ペイウォール対応）。
    失敗時は content="" で返す。
    """
    has_cookies = bool(config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE))

    # cookies.txt があれば最初からCookie付きで試みる
    if has_cookies:
        session = _build_session(cookies_file=config.COOKIES_FILE)
        result = _fetch_all_pages(url, session)
        if result.success:
            return result
        logger.info(f"Scraper: cookie-fetch failed, trying without cookies for {url}")

    # Cookie なしで試みる
    session = _build_session(cookies_file=None)
    result = _fetch_all_pages(url, session)
    if result.success:
        return result

    logger.warning(f"Scraper: failed to fetch {url}")
    return ScrapeResult(success=False, title="", content="", url=url)


def _build_session(cookies_file: str | None) -> requests.Session:
    session = requests.Session()
    session.headers.update(_HEADERS)

    if cookies_file:
        jar = http.cookiejar.MozillaCookieJar()
        try:
            jar.load(cookies_file, ignore_discard=True, ignore_expires=True)
            session.cookies = jar  # type: ignore[assignment]
            logger.info(f"Scraper: loaded cookies from {cookies_file}")
        except Exception as e:
            logger.warning(f"Scraper: failed to load cookies: {e}")

    return session


def _fetch_page_html(url: str, session: requests.Session) -> str | None:
    """1ページ分のHTMLを取得する"""
    try:
        resp = session.get(url, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.debug(f"Scraper: HTTP error for {url}: {e}")
        return None


def _extract_next_page_url(html: str, current_url: str) -> str | None:
    """
    次ページのURLを検出する。
    rel="next"、よくある「次へ」パターン、?page=N などを検索。
    """
    from html.parser import HTMLParser

    class LinkParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.next_url: str | None = None
            self._in_next_anchor = False

        def handle_starttag(self, tag, attrs):
            if self.next_url:
                return
            attr_dict = dict(attrs)
            rel = attr_dict.get("rel", "")
            href = attr_dict.get("href", "")
            # <link rel="next"> / <a rel="next">
            if tag in ("link", "a") and rel == "next" and href:
                self.next_url = href
                return
            # aria-label や class に「次」「next」を含む <a>
            label = (attr_dict.get("aria-label", "") + attr_dict.get("class", "")).lower()
            if tag == "a" and href and any(k in label for k in ("next", "次へ", "次のページ")):
                self.next_url = href

    parser = LinkParser()
    parser.feed(html)
    if parser.next_url:
        return urljoin(current_url, parser.next_url)

    current_page = _detect_current_page(current_url)
    next_page = current_page + 1

    # URLパターンで次ページを推測
    # 対応形式:
    #   ?page=2  ?p=2  /page/2  /2  (末尾の数字)  /articles/-/xxxxx/2
    patterns = [
        r'href=["\']([^"\']*[?&]page=' + str(next_page) + r'[^"\']*)["\']',
        r'href=["\']([^"\']*[?&]p=' + str(next_page) + r'[^"\']*)["\']',
        r'href=["\']([^"\'"]*/page/' + str(next_page) + r'/?[^"\']*)["\']',
        r'href=["\']([^"\'"]*/articles/[^"\']*/' + str(next_page) + r'/?)["\']',  # Diamond Online等
        r'href=["\']([^"\'"]*/\d+/' + str(next_page) + r'/?)["\']',
    ]

    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return urljoin(current_url, m.group(1))

    return None


def _detect_current_page(url: str) -> int:
    """URLから現在のページ番号を検出（デフォルト1）"""
    # /articles/-/xxxxx/2 形式（末尾の数字）
    m = re.search(r'/(\d+)/?$', urlparse(url).path)
    if m and int(m.group(1)) > 1:
        return int(m.group(1))
    # ?page=N / ?p=N
    m = re.search(r'[?&]p(?:age)?=(\d+)', url)
    return int(m.group(1)) if m else 1


def _fetch_all_pages(url: str, session: requests.Session) -> ScrapeResult:
    """
    全ページを取得してテキストを結合する。
    最大 _MAX_PAGES ページまで。
    """
    all_texts: list[str] = []
    title = ""
    visited: set[str] = set()
    current_url = url
    pages_fetched = 0

    while current_url and current_url not in visited and pages_fetched < _MAX_PAGES:
        visited.add(current_url)
        html = _fetch_page_html(current_url, session)
        if not html:
            break

        # テキスト抽出
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if not text:
            if pages_fetched == 0:
                break
            # 2ページ目以降でテキストなしはページ終端とみなす
            break

        all_texts.append(text)
        pages_fetched += 1

        # タイトルは1ページ目のみ取得
        if not title:
            meta = trafilatura.extract_metadata(html)
            title = meta.title if meta and meta.title else url

        # 次ページを検出
        current_url = _extract_next_page_url(html, current_url)
        if current_url and current_url in visited:
            break

    if not all_texts:
        return ScrapeResult(success=False, title="", content="", url=url)

    if pages_fetched > 1:
        logger.info(f"Scraper: fetched {pages_fetched} pages for {url}")

    combined = "\n\n---\n\n".join(all_texts)
    return ScrapeResult(
        success=True,
        title=title,
        content=combined,
        url=url,
        pages_fetched=pages_fetched,
    )

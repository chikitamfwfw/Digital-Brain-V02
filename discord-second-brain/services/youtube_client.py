from __future__ import annotations
import asyncio
import logging
import os
import re
import tempfile
from dataclasses import dataclass

import config

logger = logging.getLogger(__name__)

# ドメイン全体を動画として扱うサイト
_VIDEO_DOMAINS = [
    "vimeo.com",
    "dailymotion.com",
    "nicovideo.jp",
    "tver.jp",
    "abema.tv",
]

# URLパターンで動画かどうか判定するサイト（記事も混在するサイト）
_VIDEO_URL_PATTERNS = [
    # NewsPicks: /movie-series/ や movieId= を含む場合のみ動画
    r"newspicks\.com/.*(movie-series|movie/|movieId=|/programs/)",
]


def is_supported_video_url(url: str) -> bool:
    """YouTube または対応動画プラットフォームのURLか判定"""
    if _is_youtube(url):
        return True
    if any(domain in url for domain in _VIDEO_DOMAINS):
        return True
    if any(re.search(p, url) for p in _VIDEO_URL_PATTERNS):
        return True
    return False


def _is_youtube(url: str) -> bool:
    return bool(re.search(r"(?:youtube\.com|youtu\.be)", url))

# faster-whisper medium モデル（初回起動時に ~1.5GB ダウンロード）
_WHISPER_MODEL_SIZE = "small"
_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        logger.info(f"Loading faster-whisper model: {_WHISPER_MODEL_SIZE} (~1.5GB on first run)")
        _whisper_model = WhisperModel(_WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _whisper_model


@dataclass
class YouTubeResult:
    success: bool
    title: str
    transcript: str       # 最終的なテキスト（日本語）
    original_lang: str    # 検出言語コード
    url: str
    method: str           # "subtitle" / "whisper"


def _extract_video_id(url: str) -> str | None:
    patterns = [
        r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:embed/)([A-Za-z0-9_-]{11})",
        r"(?:shorts/)([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def _fetch_subtitle(video_id: str) -> tuple[str, str] | None:
    """
    字幕取得を試みる。
    Returns: (transcript_text, language_code) or None
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        # youtube-transcript-api 1.x: インスタンスメソッドに変更
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)

        # 日本語 → 英語 → その他の順で優先
        transcript = None
        lang = None
        for preferred in ["ja", "en"]:
            for t in transcript_list:
                if t.language_code == preferred:
                    transcript = t
                    lang = preferred
                    break
            if transcript:
                break

        # 見つからなければ最初のものを使用
        if transcript is None:
            for t in transcript_list:
                transcript = t
                lang = t.language_code
                break

        if transcript is None:
            return None

        fetched = transcript.fetch()
        # 1.x では FetchedTranscript はイテラブル、各要素に .text がある
        text = " ".join(
            snippet.text if hasattr(snippet, "text") else snippet["text"]
            for snippet in fetched
        )
        return text, lang

    except Exception as e:
        logger.info(f"Subtitle fetch failed for {video_id}: {e}")
        return None


def _download_audio(url: str, output_path: str) -> bool:
    """
    yt-dlp で音声をダウンロード。
    403エラー時はブラウザのCookieを使って再試行する。
    """
    import yt_dlp

    base_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_path + ".%(ext)s",
        "quiet": True,
        "no_warnings": True,
    }

    # 試行順: Cookie なし → cookies.txt ファイル
    attempts: list[dict] = [{}]
    if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
        attempts.append({"cookiefile": config.COOKIES_FILE})

    for extra in attempts:
        opts = {**base_opts, **extra}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
            return True
        except Exception as e:
            label = "with-cookies" if extra else "no-cookie"
            logger.info(f"yt-dlp attempt ({label}) failed: {e}")
            continue

    logger.error(f"yt-dlp: all attempts failed for {url}")
    return False


def _get_video_title(url: str) -> str:
    try:
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True}
        if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
            opts["cookiefile"] = config.COOKIES_FILE
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("title", url)
    except Exception:
        return url


def _find_downloaded_file(base_path: str) -> str | None:
    """yt-dlp がダウンロードしたファイルを拡張子込みで探す"""
    import glob
    files = glob.glob(base_path + ".*")
    # 情報ファイル(.json等)を除外して音声ファイルを返す
    audio_exts = {".mp3", ".m4a", ".webm", ".ogg", ".opus", ".wav", ".mp4", ".mkv"}
    for f in files:
        if os.path.splitext(f)[1].lower() in audio_exts:
            return f
    return files[0] if files else None


def _transcribe_audio(audio_path: str) -> tuple[str, str]:
    """faster-whisper で文字起こし。Returns: (text, language_code)"""
    model = _get_whisper_model()
    segments, info = model.transcribe(audio_path, beam_size=5)
    text = " ".join(seg.text for seg in segments)
    return text.strip(), info.language


async def fetch_transcript(url: str) -> YouTubeResult:
    """
    動画の文字起こしを取得する（YouTube・NewsPicks等対応）。
    YouTube: 字幕取得 → 失敗時 yt-dlp + faster-whisper
    その他: yt-dlp + faster-whisper（cookies.txt使用）
    英語コンテンツは Claude で日本語翻訳。
    """
    title = await asyncio.get_event_loop().run_in_executor(None, _get_video_title, url)

    # YouTubeのみ字幕APIを試みる
    subtitle_result = None
    if _is_youtube(url):
        video_id = _extract_video_id(url)
        if video_id:
            subtitle_result = await asyncio.get_event_loop().run_in_executor(
                None, _fetch_subtitle, video_id
            )

    if subtitle_result:
        transcript, lang = subtitle_result
        method = "subtitle"
    else:
        # yt-dlp + faster-whisper（YouTube以外は直接こちら）
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "audio")
            success = await asyncio.get_event_loop().run_in_executor(
                None, _download_audio, url, audio_path
            )
            if not success:
                return YouTubeResult(success=False, title=title, transcript="", original_lang="", url=url, method="whisper")

            # yt-dlp が付けた拡張子を自動検出
            actual_path = _find_downloaded_file(audio_path)
            if not actual_path:
                return YouTubeResult(success=False, title=title, transcript="", original_lang="", url=url, method="whisper")

            transcript, lang = await asyncio.get_event_loop().run_in_executor(
                None, _transcribe_audio, actual_path
            )
            method = "whisper"

    # 3. 英語の場合はClaudeで日本語翻訳
    if lang != "ja" and transcript:
        from services.claude_client import translate_to_japanese
        logger.info(f"Translating transcript from {lang} to Japanese")
        transcript = await translate_to_japanese(transcript)

    return YouTubeResult(
        success=True,
        title=title,
        transcript=transcript,
        original_lang=lang,
        url=url,
        method=method,
    )

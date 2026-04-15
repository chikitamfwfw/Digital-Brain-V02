from datetime import datetime, timezone


def generate_zk_id() -> str:
    """ZettelkastenノートID生成: ZK-YYYYMMDD-HHMMSS"""
    return datetime.now(timezone.utc).strftime("ZK-%Y%m%d-%H%M%S")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def render_fleeting_note(
    zk_id: str,
    raw_text: str,
    claude_summary: str,
    tags: list[str],
    references: list[str],
    template: str,
) -> str:
    """fleeting-note テンプレートを埋める"""
    tag_str = ", ".join(f"#{t}" for t in tags) if tags else ""
    ref_str = "\n".join(f"- {r}" for r in references) if references else "なし"
    return (
        template
        .replace("{{ZK_ID}}", zk_id)
        .replace("{{DATE}}", today_str())
        .replace("{{RAW_TEXT}}", raw_text.strip())
        .replace("{{SUMMARY}}", claude_summary.strip())
        .replace("{{TAGS}}", tag_str)
        .replace("{{REFERENCES}}", ref_str)
    )


def render_literature_article_note(
    zk_id: str,
    url: str,
    title: str,
    summary: str,
    key_points: str,
    tags: list[str],
    references: list[str],
    template: str,
) -> str:
    """literature-article テンプレートを埋める"""
    tag_str = ", ".join(f"#{t}" for t in tags) if tags else ""
    ref_str = "\n".join(f"- {r}" for r in references) if references else "なし"
    return (
        template
        .replace("{{ZK_ID}}", zk_id)
        .replace("{{DATE}}", today_str())
        .replace("{{URL}}", url)
        .replace("{{TITLE}}", title)
        .replace("{{SUMMARY}}", summary.strip())
        .replace("{{KEY_POINTS}}", key_points.strip())
        .replace("{{TAGS}}", tag_str)
        .replace("{{REFERENCES}}", ref_str)
    )


def render_literature_youtube_note(
    zk_id: str,
    url: str,
    title: str,
    transcript_excerpt: str,
    summary: str,
    key_points: str,
    tags: list[str],
    references: list[str],
    template: str,
) -> str:
    """literature-youtube テンプレートを埋める"""
    tag_str = ", ".join(f"#{t}" for t in tags) if tags else ""
    ref_str = "\n".join(f"- {r}" for r in references) if references else "なし"
    return (
        template
        .replace("{{ZK_ID}}", zk_id)
        .replace("{{DATE}}", today_str())
        .replace("{{URL}}", url)
        .replace("{{TITLE}}", title)
        .replace("{{TRANSCRIPT_EXCERPT}}", transcript_excerpt.strip())
        .replace("{{SUMMARY}}", summary.strip())
        .replace("{{KEY_POINTS}}", key_points.strip())
        .replace("{{TAGS}}", tag_str)
        .replace("{{REFERENCES}}", ref_str)
    )


def render_permanent_note(
    zk_id: str,
    idea: str,
    elaboration: str,
    backlinks: list[str],
    tags: list[str],
    template: str,
) -> str:
    """permanent-note テンプレートを埋める"""
    tag_str = ", ".join(f"#{t}" for t in tags) if tags else ""
    backlink_str = "\n".join(f"- [[{b}]]" for b in backlinks) if backlinks else "なし"
    return (
        template
        .replace("{{ZK_ID}}", zk_id)
        .replace("{{DATE}}", today_str())
        .replace("{{IDEA}}", idea.strip())
        .replace("{{ELABORATION}}", elaboration.strip())
        .replace("{{BACKLINKS}}", backlink_str)
        .replace("{{TAGS}}", tag_str)
    )


def truncate_for_discord(text: str, limit: int = 1900) -> str:
    """Discord メッセージ上限に収まるようにトリミング"""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n…（続きはGitHubで確認）"

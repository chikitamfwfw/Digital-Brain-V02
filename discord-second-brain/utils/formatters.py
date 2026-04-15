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
    title_ja: str,
    interpretation: str,
    significance: str,
    questions: str,
    tags: list,
    references: list,
    template: str,
) -> str:
    """fleeting-note テンプレートを埋める"""
    tag_str = ", ".join(f"#{t}" for t in tags) if tags else ""
    ref_str = "\n".join(f"- {r}" for r in references) if references else "なし"
    return (
        template
        .replace("{{ZK_ID}}", zk_id)
        .replace("{{DATE}}", today_str())
        .replace("{{TAGS}}", tag_str)
        .replace("{{TITLE_JA}}", title_ja.strip() if title_ja else zk_id)
        .replace("{{RAW_TEXT}}", raw_text.strip())
        .replace("{{INTERPRETATION}}", interpretation.strip() if interpretation else "")
        .replace("{{SIGNIFICANCE}}", significance.strip() if significance else "")
        .replace("{{QUESTIONS}}", questions.strip() if questions else "")
        .replace("{{REFERENCES}}", ref_str)
    )


def render_literature_article_note(
    zk_id: str,
    url: str,
    title: str,
    summary: str,
    key_points: str,
    details: str,
    insights: str,
    personal_application: str,
    open_questions: str,
    tags: list,
    references: list,
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
        .replace("{{SUMMARY}}", summary.strip() if summary else "")
        .replace("{{KEY_POINTS}}", key_points.strip() if key_points else "")
        .replace("{{DETAILS}}", details.strip() if details else "")
        .replace("{{INSIGHTS}}", insights.strip() if insights else "")
        .replace("{{PERSONAL_APPLICATION}}", personal_application.strip() if personal_application else "")
        .replace("{{OPEN_QUESTIONS}}", open_questions.strip() if open_questions else "")
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
    details: str,
    insights: str,
    personal_application: str,
    open_questions: str,
    tags: list,
    references: list,
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
        .replace("{{SUMMARY}}", summary.strip() if summary else "")
        .replace("{{KEY_POINTS}}", key_points.strip() if key_points else "")
        .replace("{{DETAILS}}", details.strip() if details else "")
        .replace("{{INSIGHTS}}", insights.strip() if insights else "")
        .replace("{{PERSONAL_APPLICATION}}", personal_application.strip() if personal_application else "")
        .replace("{{OPEN_QUESTIONS}}", open_questions.strip() if open_questions else "")
        .replace("{{TRANSCRIPT_EXCERPT}}", transcript_excerpt.strip() if transcript_excerpt else "")
        .replace("{{TAGS}}", tag_str)
        .replace("{{REFERENCES}}", ref_str)
    )


def render_permanent_note(
    zk_id: str,
    title: str,
    thesis: str,
    elaboration: str,
    significance: str,
    application: str,
    limitations: str,
    backlinks: list,
    tags: list,
    template: str,
) -> str:
    """permanent-note テンプレートを埋める"""
    tag_str = ", ".join(f"#{t}" for t in tags) if tags else ""
    backlink_str = "\n".join(f"- [[{b}]]" for b in backlinks) if backlinks else "なし"
    return (
        template
        .replace("{{ZK_ID}}", zk_id)
        .replace("{{DATE}}", today_str())
        .replace("{{TAGS}}", tag_str)
        .replace("{{TITLE}}", title.strip() if title else zk_id)
        .replace("{{THESIS}}", thesis.strip() if thesis else "")
        .replace("{{ELABORATION}}", elaboration.strip() if elaboration else "")
        .replace("{{SIGNIFICANCE}}", significance.strip() if significance else "")
        .replace("{{APPLICATION}}", application.strip() if application else "")
        .replace("{{LIMITATIONS}}", limitations.strip() if limitations else "")
        .replace("{{BACKLINKS}}", backlink_str)
    )


def truncate_for_discord(text: str, limit: int = 1900) -> str:
    """Discord メッセージ上限に収まるようにトリミング"""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n…（続きはGitHubで確認）"

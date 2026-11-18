"""Telegram message formatting and delivery."""
import html
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

MAX_MESSAGE_LENGTH = 4096
SGT = timezone(timedelta(hours=8))


def _escape(value: Any, limit: int = 1500) -> str:
    return html.escape(str(value or "").strip()[:limit], quote=True)


def _display_date(value: Optional[str]) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(SGT).strftime("%d %b %Y")


def format_article(article: Dict[str, Any]) -> str:
    decision = article.get("decision") or {}
    summary = article.get("summary") or {}

    meta = " · ".join(part for part in (_display_date(article.get("published_at")), _escape(article.get("source"), 100)) if part)
    tags = " · ".join(part for part in (_escape(decision.get("country"), 100), _escape(decision.get("news_type"), 100)) if part)

    lines = [f"<b>{_escape(article.get('title'), 300)}</b>"]
    lines.extend(part for part in (meta, tags) if part)
    if summary.get("summary"):
        lines.append(f"\n{_escape(summary['summary'])}")
    if summary.get("key_figures"):
        lines.append(f"\n<b>Key figures:</b> {_escape('; '.join(summary['key_figures']), 500)}")
    if summary.get("key_parties"):
        lines.append(f"<b>Key parties:</b> {_escape('; '.join(summary['key_parties']), 500)}")
    lines.append(f'\n<a href="{_escape(article.get("url"), 1000)}">Read article</a>')
    return "\n".join(lines)


def edition_name(now: datetime) -> str:
    local = now.astimezone(SGT)
    label = "🌅 Morning brief" if local.hour < 12 else "🌆 Evening brief"
    return f"{label} · {local.strftime('%d %b %Y')}"


def brief_id(now: datetime) -> str:
    local = now.astimezone(SGT)
    return f"{local.strftime('%Y-%m-%d')}-{'AM' if local.hour < 12 else 'PM'}"


def build_brief_messages(
    articles: List[Dict[str, Any]], edition: str
) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Group articles into messages under Telegram's limit, tracking which articles each holds."""
    separator = "\n\n———\n\n"
    messages: List[Tuple[str, List[Dict[str, Any]]]] = []
    text = f"<b>{_escape(edition, 200)}</b>"
    group: List[Dict[str, Any]] = []

    for article in articles:
        block = format_article(article)
        if group and len(text) + len(separator) + len(block) > MAX_MESSAGE_LENGTH:
            messages.append((text, group))
            text, group = block, [article]
            continue
        text = f"{text}{separator}{block}"
        group.append(article)

    if group:
        messages.append((text, group))
    return messages


def format_sheet_alert(new_articles: int, sheet_url: str) -> str:
    """Announce new rating rows without revealing which articles the bot selected."""
    noun = "article is" if new_articles == 1 else "articles are"
    text = f"<b>📊 Rating sheet updated</b>\n{new_articles} new {noun} ready to rate."
    if sheet_url.lower().startswith("https://"):
        text += f'\n\n<a href="{_escape(sheet_url, 1000)}">Open the rating sheet</a>'
    return text


def send_telegram_message(text: str) -> bool:
    """Send one HTML message and report whether Telegram accepted it."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    payload = json.dumps({
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
        return True
    except Exception as exc:
        print(f"Telegram send failed: {type(exc).__name__}: {getattr(exc, 'code', '')}")
        return False

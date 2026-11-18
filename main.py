"""Entry point: collect articles every 2 hours and send a morning and evening brief."""
import argparse
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv()

from src import ranking, storage  # noqa: E402
from src.feeds import NEWS_QUERIES, RSS_FEEDS  # noqa: E402
from src.llm import classify_news_articles, set_gemini_deadline, summarize_articles  # noqa: E402
from src.search_sources import (  # noqa: E402
    fetch_finlight_articles,
    fetch_newsdata_articles,
    fetch_rss_articles,
)
from src.sheet_sync import is_configured as rating_sheet_configured, sync_ratings_sheet  # noqa: E402
from src.telegram import (  # noqa: E402
    brief_id,
    build_brief_messages,
    edition_name,
    format_sheet_alert,
    send_telegram_message,
)  # noqa: E402

NEWSDATA_API = os.environ.get("NEWSDATA_API_KEY", "")
FINLIGHT_API = os.environ.get("FINLIGHT_API_KEY", "")

SHORTLIST_SIZE = 40
BATCH_SIZE = 20
MAX_ARTICLES_PER_BRIEF = 12
RECENT_EVENT_DAYS = 3
LAMBDA_SAFETY_MARGIN_SECONDS = 60
SHEET_SYNC_MIN_SECONDS = 45


def _briefs_paused() -> bool:
    """Pause article briefs only when the rating sheet can receive the articles instead."""
    paused = os.environ.get("PAUSE_TELEGRAM_BRIEFS", "").strip().lower() == "true"
    if paused and not rating_sheet_configured():
        print("PAUSE_TELEGRAM_BRIEFS ignored because the rating sheet is not set up.")
        return False
    return paused


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status, "body": json.dumps(body)}


def _counts(articles: List[Dict[str, Any]]) -> Dict[str, int]:
    return dict(Counter(article.get("feed") or article.get("provider") or "unknown" for article in articles))


def fetch_all_articles() -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    if NEWSDATA_API:
        articles.extend(fetch_newsdata_articles(NEWS_QUERIES, NEWSDATA_API))
    if FINLIGHT_API:
        articles.extend(fetch_finlight_articles(NEWS_QUERIES, FINLIGHT_API))
    articles.extend(fetch_rss_articles(RSS_FEEDS))
    return articles


def _recent_sent_titles(history: List[Dict[str, Any]], now: datetime) -> List[str]:
    cutoff = now - timedelta(days=RECENT_EVENT_DAYS)
    titles = []
    for record in history:
        updated = record.get("updated_at")
        if record.get("status") == "sent" and updated and datetime.fromisoformat(updated) >= cutoff:
            titles.append(record.get("title", ""))
    return titles


def run_collect(dry_run: bool = False) -> Dict[str, Any]:
    """Fetch, filter and queue new articles. No LLM calls happen in this step."""
    now = _now()
    history = storage.load_history()
    state = storage.load_pending()
    fetched = fetch_all_articles()

    known = {record.get("hash") for record in history}
    known.update(storage.hash_url(article.get("url", "")) for article in state["articles"])

    fresh = [article for article in fetched if ranking.is_fresh(article, now)]
    new_articles = []
    for article in fresh:
        key = storage.hash_url(article.get("url", ""))
        if article.get("url") and key not in known:
            known.add(key)
            new_articles.append({**article, "first_seen": now.isoformat()})

    relevant, removed = [], []
    for article in new_articles:
        article["score"] = ranking.score_breakdown(article)
        reason = ranking.rejection_reason(article)
        if reason:
            removed.append({**article, "reason": reason})
        else:
            relevant.append(article)
    kept, duplicates = ranking.drop_same_event(
        state["articles"] + relevant, recent_titles=_recent_sent_titles(history, now)
    )

    stats = {
        "fetched": _counts(fetched),
        "fresh": len(fresh),
        "new": len(new_articles),
        "passed_code_filter": _counts(relevant),
        "same_event_dropped": len(duplicates),
        "pending": len(kept),
        "dry_run": dry_run,
    }
    print(json.dumps({"stage": "collect", **stats}))

    if dry_run:
        return _response(200, stats)

    for article in kept:
        if not article.get("decision"):
            storage.record_article(history, article, "pending", now)
    for article in duplicates:
        storage.record_article(history, article, "duplicate_event", now)

    state["articles"] = kept
    storage.save_history(storage.prune_history(history, now))
    storage.save_pending(state)
    storage.add_code_rejections(removed, now)

    if state["brief_due"]:
        print("Retrying the brief that could not finish earlier.")
        return run_brief()
    return _response(200, stats)


def _save(history: List[Dict[str, Any]], state: Dict[str, Any], dry_run: bool) -> None:
    if not dry_run:
        storage.save_history(history)
        storage.save_pending(state)


def _defer_brief(history, state, dry_run: bool, exc: Exception) -> Dict[str, Any]:
    print(f"Brief deferred: {exc}")
    state["brief_due"] = True
    _save(history, state, dry_run)
    return _response(503, {"error": "Gemini unavailable", "brief_due": True})


def run_brief(dry_run: bool = False) -> Dict[str, Any]:
    """Filter pending articles with Gemini, summarise the best and send them to Telegram."""
    now = _now()
    history = storage.load_history()
    state = storage.load_pending()

    already_sent = {record.get("hash") for record in history if record.get("status") == "sent"}
    unsent = [a for a in state["articles"] if storage.hash_url(a.get("url", "")) not in already_sent]

    pending = []
    for article in unsent:
        if ranking.is_fresh(article, now):
            pending.append(article)
        else:
            storage.record_article(history, article, "expired", now)
    state["articles"] = pending
    state["brief_due"] = True  # Stays set if AWS stops this run, so the next collect retries it.
    _save(history, state, dry_run)

    undecided = [article for article in pending if not article.get("decision")]
    candidates = ranking.shortlist(undecided, limit=SHORTLIST_SIZE)
    for index, article in enumerate(candidates, start=1):
        article["id"] = index
        article["shortlist_rank"] = index
        article.setdefault("score", ranking.score_breakdown(article))

    for start in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[start:start + BATCH_SIZE]
        try:
            decisions = classify_news_articles(batch)
        except Exception as exc:
            return _defer_brief(history, state, dry_run, exc)

        by_id = {article["id"]: article for article in batch}
        for decision in decisions:
            article = by_id[decision["id"]]
            article["decision"] = decision
            status = "accepted" if decision["should_send"] else "rejected"
            storage.record_article(history, article, status, now, decision.get("evidence"))
        state["articles"] = [
            article for article in state["articles"]
            if (article.get("decision") or {}).get("should_send", True)
        ]
        _save(history, state, dry_run)

    accepted = [article for article in state["articles"] if article.get("decision")]
    selected = ranking.shortlist(accepted, limit=MAX_ARTICLES_PER_BRIEF, per_source=MAX_ARTICLES_PER_BRIEF)
    for position, article in enumerate(selected, start=1):
        article["brief_rank"] = position
    selected_urls = {article["url"] for article in selected}
    for article in accepted:
        if article["url"] not in selected_urls:
            article["missed_cap"] = True
            storage.record_article(history, article, "accepted", now)

    paused = _briefs_paused()
    needs_summary = [] if paused else [article for article in selected if "summary" not in article]
    for index, article in enumerate(needs_summary, start=1):
        article["id"] = index
    if needs_summary:
        try:
            summaries = summarize_articles(needs_summary)
        except Exception as exc:
            return _defer_brief(history, state, dry_run, exc)
        for article in needs_summary:
            article["summary"] = summaries.get(article["id"], {})
        _save(history, state, dry_run)

    sent_count = 0
    if paused and selected:
        for article in selected:
            article["delivery"] = "sheet_only"
            article["brief_id"] = brief_id(now)
            storage.record_article(history, article, "sent", now, (article.get("decision") or {}).get("evidence"))
        selected_urls = {article["url"] for article in selected}
        state["articles"] = [article for article in state["articles"] if article["url"] not in selected_urls]
        state["sheet_alert_due"] = True
        sent_count = len(selected)
        print(f"Telegram briefs paused: {sent_count} articles selected for the rating sheet.")

    for text, group in [] if paused else build_brief_messages(selected, edition_name(now)):
        if dry_run:
            print(text)
            continue
        if not send_telegram_message(text):
            continue
        sent_urls = {article["url"] for article in group}
        for article in group:
            article["brief_id"] = brief_id(now)
            storage.record_article(history, article, "sent", now, (article.get("decision") or {}).get("evidence"))
        state["articles"] = [article for article in state["articles"] if article["url"] not in sent_urls]
        sent_count += len(group)
        _save(history, state, dry_run)

    state["brief_due"] = False
    _save(history, state, dry_run)

    stats = {
        "shortlisted": _counts(candidates),
        "accepted": len(accepted),
        "selected": len(selected),
        "sent": sent_count,
        "pending": len(state["articles"]),
        "dry_run": dry_run,
    }
    print(json.dumps({"stage": "brief", **stats}))
    return _response(200, stats)


def _update_rating_sheet(context: Any) -> None:
    """Update the client's rating sheet without ever failing the news run."""
    def has_time() -> bool:
        return context is None or context.get_remaining_time_in_millis() > SHEET_SYNC_MIN_SECONDS * 1000

    try:
        added = sync_ratings_sheet(should_continue=has_time) or 0
        if _briefs_paused():
            _announce_rating_sheet(added)
    except Exception as exc:
        print(f"Rating sheet update failed: {type(exc).__name__}")


def _announce_rating_sheet(added: int) -> None:
    """After a brief, send one Telegram alert once its articles have reached the sheet."""
    state = storage.load_pending()
    state["sheet_rows_unannounced"] += added

    if state["sheet_alert_due"] and state["sheet_rows_unannounced"] > 0:
        alert = format_sheet_alert(state["sheet_rows_unannounced"], os.environ.get("RATING_SHEET_URL", "").strip())
        if send_telegram_message(alert):
            state["sheet_alert_due"] = False
            state["sheet_rows_unannounced"] = 0

    storage.save_pending(state)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda entry point. Events without a mode run the 2-hourly collect step."""
    event = event or {}
    mode = event.get("mode", "collect")
    dry_run = event.get("dry_run") is True
    if context is not None:
        set_gemini_deadline(context.get_remaining_time_in_millis() / 1000 - LAMBDA_SAFETY_MARGIN_SECONDS)

    if mode == "collect":
        result = run_collect(dry_run=dry_run)
    elif mode == "brief":
        result = run_brief(dry_run=dry_run)
    else:
        return _response(400, {"error": "mode must be collect or brief"})

    if not dry_run:
        _update_rating_sheet(context)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the news pipeline locally.")
    parser.add_argument("mode", choices=["collect", "brief", "report"])
    parser.add_argument("--dry-run", action="store_true", help="Print results without sending or saving.")
    parser.add_argument("--days", type=int, default=7, help="Report: number of trial days to include.")
    parser.add_argument("--from-s3", action="store_true", help="Report: read trial data from your S3 bucket.")
    parser.add_argument("--output", help="Report: spreadsheet path to create.")
    args = parser.parse_args()

    if args.mode == "report":
        if args.from_s3:
            os.environ["USE_S3_STATE"] = "1"
        from src.report import run_report

        print(f"Report created: {run_report(args.days, args.output)}")
    else:
        print(lambda_handler({"mode": args.mode, "dry_run": args.dry_run}, None))

"""Shared definitions for trial articles, used by the rating sheet and the trial report."""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

GROUP_SENT = "Sent"
GROUP_CODE = "Removed by code filter"
GROUP_REJECTED = "Rejected by Gemini"
GROUP_MISSED_CAP = "Accepted, not sent"
SPLIT_TUNE = "Tune"
SPLIT_CHECK = "Check"
CHECK_DAYS = 2
REASONS = ["Off-topic", "Too minor", "Wrong region", "Duplicate", "Old news", "Not Interested"]
SGT = timezone(timedelta(hours=8))

# Rating columns come first. Analysis columns are collapsed so they don't influence the rating.
RATE_COLUMNS = [
    ("Useful", 10), ("Reason if not useful", 18), ("Title", 60), ("Description", 60), ("Source", 20),
    ("Published", 13),
]
ANALYSIS_COLUMNS = [
    ("Country", 14), ("News type", 24), ("Group", 22), ("Trial split", 10), ("Feed", 26), ("Provider", 10),
    ("Status", 14), ("Brief", 14), ("Brief position", 9), ("Shortlist position", 9), ("Total score", 8),
    ("Strong terms", 8), ("Deal terms", 8), ("Sector terms", 8), ("Amount", 8), ("Source trust", 8),
    ("Matched terms", 34), ("Gemini evidence", 50), ("Code filter reason", 18), ("Link", 40),
]
HEADERS = [name for name, _ in RATE_COLUMNS + ANALYSIS_COLUMNS]

SHEET_VISIBLE_COLUMNS = 7
SHEET_HEADERS = [
    "Useful", "Reason if not useful", "Title", "Link", "Description", "Source", "Published",
    *[name for name, _ in ANALYSIS_COLUMNS if name != "Link"],
    "Hash",
]


def parse_time(value: Optional[str]) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def article_group(record: Dict[str, Any]) -> Optional[str]:
    if record.get("status") == "code_rejected":
        return GROUP_CODE
    if record.get("status") == "sent":
        return GROUP_SENT
    if record.get("gemini_decision") == "rejected":
        return GROUP_REJECTED
    if record.get("gemini_decision") == "accepted":
        return GROUP_MISSED_CAP
    return None


def _published(record: Dict[str, Any]) -> str:
    moment = parse_time(record.get("published_at"))
    return moment.astimezone(SGT).strftime("%d %b %Y") if moment else ""


def row_values(record: Dict[str, Any], group: str, split: str) -> Dict[str, Any]:
    score = record.get("score") or {}
    terms = score.get("matched_terms") or []
    return {
        "Title": record.get("title", ""), "Description": record.get("description", ""),
        "Source": record.get("source", ""), "Published": _published(record),
        "Country": record.get("country", ""), "News type": record.get("news_type", ""),
        "Group": group, "Trial split": split, "Feed": record.get("feed") or record.get("provider") or "unknown",
        "Provider": record.get("provider", ""), "Status": record.get("status", ""),
        "Brief": record.get("brief_id", ""), "Brief position": record.get("brief_rank"),
        "Shortlist position": record.get("shortlist_rank"), "Total score": score.get("total"),
        "Strong terms": score.get("strong"), "Deal terms": score.get("deal"), "Sector terms": score.get("sector"),
        "Amount": score.get("amount"), "Source trust": score.get("trust"),
        "Matched terms": f"|{'|'.join(terms)}|" if terms else "", "Gemini evidence": record.get("evidence", ""),
        "Code filter reason": record.get("reason", ""), "Link": record.get("url", ""),
    }

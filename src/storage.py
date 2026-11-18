"""Saved state: article history and the pending list, kept in S3 on AWS or locally otherwise."""
import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HISTORY_KEY = "seen_urls.json"
PENDING_KEY = "pending_articles.json"
CODE_REJECTIONS_KEY = "code_rejections.json"
HISTORY_RETENTION_DAYS = 30
CODE_REJECTION_RETENTION_DAYS = 14
TRIAL_FIELDS = ("score", "shortlist_rank", "brief_rank", "brief_id", "missed_cap", "delivery")

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "cmpid", "ocid", "igshid"}


def _s3_bucket() -> Optional[str]:
    if os.environ.get("AWS_EXECUTION_ENV") or os.environ.get("USE_S3_STATE"):
        return os.environ.get("S3_BUCKET_NAME") or None
    return None


def _read_json(key: str) -> Any:
    bucket = _s3_bucket()
    if bucket:
        import boto3
        from botocore.exceptions import ClientError

        try:
            response = boto3.client("s3").get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise
        return json.loads(response["Body"].read().decode("utf-8"))

    try:
        with (DATA_DIR / key).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_json(key: str, payload: Any) -> None:
    body = json.dumps(payload, indent=2)
    bucket = _s3_bucket()
    if bucket:
        import boto3

        boto3.client("s3").put_object(
            Bucket=bucket, Key=key, Body=body.encode("utf-8"), ContentType="application/json"
        )
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / key).write_text(body, encoding="utf-8")


def normalize_url(url: str) -> str:
    """Remove fragments and tracking parameters so one article keeps one identity."""
    parts = urlsplit((url or "").strip())
    kept = [
        part for part in parts.query.split("&")
        if part and not part.split("=")[0].lower().startswith("utm_")
        and part.split("=")[0].lower() not in TRACKING_PARAMS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), ""))


def hash_url(url: str) -> str:
    return hashlib.sha256(normalize_url(url).lower().encode("utf-8")).hexdigest()


def load_history() -> List[Dict[str, Any]]:
    payload = _read_json(HISTORY_KEY)
    records = payload.get("seen_hashes", []) if isinstance(payload, dict) else payload or []
    return [record for record in records if isinstance(record, dict)]


def save_history(records: List[Dict[str, Any]]) -> None:
    _write_json(HISTORY_KEY, {"seen_hashes": records})


def load_pending() -> Dict[str, Any]:
    payload = _read_json(PENDING_KEY)
    if not isinstance(payload, dict):
        payload = {}
    return {
        "articles": payload.get("articles", []),
        "brief_due": bool(payload.get("brief_due", False)),
        "sheet_alert_due": bool(payload.get("sheet_alert_due", False)),
        "sheet_rows_unannounced": int(payload.get("sheet_rows_unannounced", 0)),
    }


def save_pending(state: Dict[str, Any]) -> None:
    _write_json(PENDING_KEY, state)


def record_article(
    history: List[Dict[str, Any]],
    article: Dict[str, Any],
    status: str,
    now: datetime,
    evidence: Optional[str] = None,
) -> None:
    """Add or update an article's history record in place."""
    key = hash_url(article.get("url", ""))
    record = next((item for item in history if item.get("hash") == key), None)
    if record is None:
        record = {"hash": key, "first_seen": now.isoformat()}
        history.append(record)

    record.update({
        "url": article.get("url", ""),
        "title": article.get("title", ""),
        "source": article.get("source", ""),
        "provider": article.get("provider", ""),
        "feed": article.get("feed", ""),
        "description": article.get("description", ""),
        "published_at": article.get("published_at", ""),
        "status": status,
        "updated_at": now.isoformat(),
    })
    if evidence:
        record["evidence"] = evidence

    decision = article.get("decision") or {}
    if decision:
        record["gemini_decision"] = "accepted" if decision.get("should_send") else "rejected"
        record["country"] = decision.get("country", "")
        record["news_type"] = decision.get("news_type", "")
    for field in TRIAL_FIELDS:
        if field in article:
            record[field] = article[field]


def load_code_rejections() -> List[Dict[str, Any]]:
    payload = _read_json(CODE_REJECTIONS_KEY)
    return [record for record in payload or [] if isinstance(record, dict)]


def save_code_rejections(records: List[Dict[str, Any]]) -> None:
    _write_json(CODE_REJECTIONS_KEY, records)


def add_code_rejections(articles: List[Dict[str, Any]], now: datetime) -> None:
    """Log articles removed by the code filters once each, keeping the last 14 days."""
    cutoff = now - timedelta(days=CODE_REJECTION_RETENTION_DAYS)
    records = [
        record for record in load_code_rejections()
        if datetime.fromisoformat(record["first_seen"]) >= cutoff
    ]
    known = {record.get("hash") for record in records}
    for article in articles:
        key = hash_url(article.get("url", ""))
        if key in known:
            continue
        known.add(key)
        records.append({
            "hash": key,
            "url": article.get("url", ""),
            "title": article.get("title", ""),
            "source": article.get("source", ""),
            "provider": article.get("provider", ""),
            "feed": article.get("feed", ""),
            "description": article.get("description", ""),
            "published_at": article.get("published_at", ""),
            "first_seen": now.isoformat(),
            "status": "code_rejected",
            "reason": article.get("reason", ""),
            "score": article.get("score", {}),
        })
    save_code_rejections(records)


def prune_history(history: List[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    """Drop dated records past the retention window. Older records without dates are kept."""
    cutoff = now - timedelta(days=HISTORY_RETENTION_DAYS)
    kept = []
    for record in history:
        first_seen = record.get("first_seen")
        if not first_seen or datetime.fromisoformat(first_seen) >= cutoff:
            kept.append(record)
    return kept

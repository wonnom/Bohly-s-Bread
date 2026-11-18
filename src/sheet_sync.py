"""Adds trial articles to the client's Google Sheet so they can be rated during the trial."""
import hashlib
import json
import os
import re
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from src import storage
from src.trial_data import (
    CHECK_DAYS,
    GROUP_CODE,
    GROUP_MISSED_CAP,
    GROUP_REJECTED,
    GROUP_SENT,
    SGT,
    SHEET_HEADERS,
    SPLIT_CHECK,
    SPLIT_TUNE,
    article_group,
    parse_time,
    row_values,
)

TRIAL_DAYS = 7
SAMPLE_PER_GROUP_PER_DAY = 3
BATCH_SIZE = 50
MAX_BATCHES_PER_RUN = 4
REQUEST_TIMEOUT_SECONDS = 30
MAX_CELL_LENGTH = 2000
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _post_rows(url: str, secret: str, rows: List[List[Any]]) -> Set[str]:
    """Send rows to the sheet and return the article hashes it confirmed."""
    body = json.dumps({"secret": secret, "headers": SHEET_HEADERS, "rows": rows}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("ok") is not True:
        raise RuntimeError(f"sheet returned {payload.get('error', 'an unknown error')}")
    return {str(value) for value in payload.get("received") or []}


def _safe_cell(value: Any) -> Any:
    """Clean text so a headline can never run as a spreadsheet formula."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    text = CONTROL_CHARACTERS.sub("", value)[:MAX_CELL_LENGTH]
    return f"'{text}" if text[:1] in ("=", "+", "-", "@") else text


def _sheet_row(record: Dict[str, Any], group: str, split: str) -> List[Any]:
    values = row_values(record, group, split)
    link = str(values.get("Link") or "")
    values["Link"] = link if link.lower().startswith(("https://", "http://")) else ""
    values["Hash"] = record.get("hash", "")
    return [_safe_cell(values.get(header)) for header in SHEET_HEADERS]


def _random_order(record: Dict[str, Any]) -> str:
    return hashlib.sha256(str(record.get("hash")).encode("utf-8")).hexdigest()


def _trial_start(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=SGT)
    except ValueError:
        print("Rating sheet skipped: TRIAL_START_DATE must look like 2026-11-20")
        return None


def _sample_group(record: Dict[str, Any]) -> Optional[str]:
    """Return an unsent article's group once its outcome can no longer change."""
    group = article_group(record)
    if group in (GROUP_CODE, GROUP_REJECTED):
        return group
    if group == GROUP_MISSED_CAP and record.get("status") == "expired":
        return group
    return None


def _select_rows(records: List[Dict[str, Any]], start: datetime) -> List[Tuple[Dict[str, Any], str, str]]:
    """Choose all sent articles plus a small daily sample of unsent ones."""
    end = start + timedelta(days=TRIAL_DAYS)
    chosen: List[Tuple[Dict[str, Any], str, str]] = []
    already_added: Counter = Counter()
    waiting: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}

    def trial_day(record: Dict[str, Any]) -> Optional[int]:
        seen = parse_time(record.get("first_seen"))
        return (seen - start).days if seen and start <= seen < end else None

    def split(day: int) -> str:
        return SPLIT_CHECK if day >= TRIAL_DAYS - CHECK_DAYS else SPLIT_TUNE

    for record in records:
        day = trial_day(record)
        if day is None or not record.get("score"):
            continue
        if article_group(record) == GROUP_SENT:
            if not record.get("sheet_synced"):
                chosen.append((record, GROUP_SENT, split(day)))
            continue
        group = _sample_group(record)
        if not group:
            continue
        if record.get("sheet_synced"):
            already_added[(group, day)] += 1
        else:
            waiting.setdefault((group, day), []).append(record)

    for (group, day), items in waiting.items():
        places = max(0, SAMPLE_PER_GROUP_PER_DAY - already_added[(group, day)])
        for record in sorted(items, key=_random_order)[:places]:
            chosen.append((record, group, split(day)))

    return sorted(chosen, key=lambda item: _random_order(item[0]))


def is_configured() -> bool:
    """Return whether the bot has everything it needs to update the rating sheet."""
    url = os.environ.get("SHEET_WEBHOOK_URL", "").strip()
    secret = os.environ.get("SHEET_WEBHOOK_SECRET", "").strip()
    start_value = os.environ.get("TRIAL_START_DATE", "").strip()
    return bool(url and secret and start_value and _trial_start(start_value))


def sync_ratings_sheet(
    now: Optional[datetime] = None, should_continue: Optional[Callable[[], bool]] = None
) -> int:
    """Add new trial articles to the sheet and return how many were added.

    A failure is logged and retried on the next run.
    """
    if not is_configured():
        return 0

    url = os.environ["SHEET_WEBHOOK_URL"].strip()
    secret = os.environ["SHEET_WEBHOOK_SECRET"].strip()
    start = _trial_start(os.environ["TRIAL_START_DATE"])

    now = now or datetime.now(timezone.utc)
    history = storage.load_history()
    code_rejections = storage.load_code_rejections()
    selected = _select_rows(history + code_rejections, start)

    added = 0
    for batch_number in range(MAX_BATCHES_PER_RUN):
        batch = selected[batch_number * BATCH_SIZE:(batch_number + 1) * BATCH_SIZE]
        if not batch or (should_continue is not None and not should_continue()):
            break
        try:
            confirmed = _post_rows(url, secret, [_sheet_row(*item) for item in batch])
        except Exception as exc:
            print(f"Rating sheet update failed: {type(exc).__name__}")
            break

        for record, _, _ in batch:
            if record.get("hash") in confirmed:
                record["sheet_synced"] = True
                added += 1
        storage.save_history(history)
        storage.save_code_rejections(code_rejections)

    if added:
        print(json.dumps({"stage": "rating_sheet", "rows_added": added}))
    return added

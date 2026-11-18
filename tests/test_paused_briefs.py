import pytest

import main
from src import sheet_sync, storage, telegram
from tests.test_pipeline import approve_all

SHEET_URL = "https://docs.google.com/spreadsheets/d/abc/edit"


@pytest.fixture
def paused(state_dir, monkeypatch):
    monkeypatch.setenv("PAUSE_TELEGRAM_BRIEFS", "true")
    monkeypatch.setenv("SHEET_WEBHOOK_URL", "https://script.google.com/macros/s/abc/exec")
    monkeypatch.setenv("SHEET_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("TRIAL_START_DATE", "2026-01-01")
    monkeypatch.setenv("RATING_SHEET_URL", SHEET_URL)
    calls = {"fetched": [], "messages": [], "summaries": 0, "rows": [5], "send_ok": True}

    def send(text):
        if calls["send_ok"]:
            calls["messages"].append(text)
        return calls["send_ok"]

    def summarise(batch):
        calls["summaries"] += 1
        return {}

    monkeypatch.setattr(main, "fetch_all_articles", lambda: [dict(item) for item in calls["fetched"]])
    monkeypatch.setattr(main, "classify_news_articles", approve_all)
    monkeypatch.setattr(main, "summarize_articles", summarise)
    monkeypatch.setattr(main, "send_telegram_message", send)
    monkeypatch.setattr(main, "sync_ratings_sheet", lambda **kwargs: calls["rows"].pop(0) if calls["rows"] else 0)
    return calls


def run(mode):
    return main.lambda_handler({"mode": mode}, None)


def test_paused_brief_sends_only_a_sheet_alert(paused, make_article):
    article = make_article("Sembcorp closes $900 million green loan for Vietnam wind farm")
    paused["fetched"] = [article]
    paused["rows"] = [0, 5]
    run("collect")

    run("brief")

    assert len(paused["messages"]) == 1
    alert = paused["messages"][0]
    assert "5 new articles" in alert and SHEET_URL in alert
    assert "Sembcorp" not in alert


def test_paused_brief_skips_summaries_but_records_the_selection(paused, make_article):
    article = make_article()
    paused["fetched"] = [article]
    paused["rows"] = [0, 1]
    run("collect")

    run("brief")

    record = storage.load_history()[0]
    assert paused["summaries"] == 0
    assert record["status"] == "sent"
    assert record["delivery"] == "sheet_only"
    assert record["brief_rank"] == 1
    assert storage.load_pending()["articles"] == []


def test_collect_rows_wait_for_the_next_brief_alert(paused, make_article):
    paused["fetched"] = [make_article()]
    paused["rows"] = [3, 2]

    run("collect")
    assert paused["messages"] == []

    run("brief")
    assert len(paused["messages"]) == 1 and "5 new articles" in paused["messages"][0]


def test_alert_is_not_repeated_by_later_collect_runs(paused, make_article):
    paused["fetched"] = [make_article()]
    paused["rows"] = [0, 4, 0, 2]
    run("collect")
    run("brief")

    run("collect")
    run("collect")

    assert len(paused["messages"]) == 1


def test_failed_alert_is_retried_on_the_next_run(paused, make_article):
    paused["fetched"] = [make_article()]
    paused["rows"] = [0, 4, 0]
    run("collect")
    paused["send_ok"] = False
    run("brief")

    paused["send_ok"] = True
    run("collect")

    assert len(paused["messages"]) == 1 and "4 new articles" in paused["messages"][0]


def test_alert_waits_until_a_failed_sheet_update_succeeds(paused, make_article):
    paused["fetched"] = [make_article()]
    paused["rows"] = [0, 0, 6]
    run("collect")
    run("brief")
    assert paused["messages"] == []

    run("collect")

    assert len(paused["messages"]) == 1 and "6 new articles" in paused["messages"][0]


def test_single_article_alert_reads_naturally():
    assert "1 new article is" in telegram.format_sheet_alert(1, SHEET_URL)


def test_alert_never_links_to_a_non_web_address():
    assert "href" not in telegram.format_sheet_alert(2, "javascript:alert(1)")


def test_briefs_still_send_if_the_sheet_is_not_set_up(paused, make_article, monkeypatch):
    monkeypatch.delenv("SHEET_WEBHOOK_URL")
    article = make_article("Sembcorp closes $900 million green loan for Vietnam wind farm")
    paused["fetched"] = [article]
    run("collect")

    run("brief")

    assert any("Sembcorp" in message for message in paused["messages"])


def test_briefs_send_normally_when_not_paused(paused, make_article, monkeypatch):
    monkeypatch.delenv("PAUSE_TELEGRAM_BRIEFS")
    article = make_article("Sembcorp closes $900 million green loan for Vietnam wind farm")
    paused["fetched"] = [article]
    run("collect")

    run("brief")

    assert any("Sembcorp" in message for message in paused["messages"])
    assert paused["summaries"] == 1


def test_dry_run_sends_no_alert_and_records_nothing(paused, make_article):
    paused["fetched"] = [make_article()]
    paused["rows"] = [0]
    run("collect")

    main.lambda_handler({"mode": "brief", "dry_run": True}, None)

    assert paused["messages"] == []
    assert len(storage.load_pending()["articles"]) == 1


def test_sheet_configuration_check(monkeypatch):
    monkeypatch.setenv("SHEET_WEBHOOK_URL", "https://script.google.com/x")
    monkeypatch.setenv("SHEET_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("TRIAL_START_DATE", "not a date")
    assert sheet_sync.is_configured() is False

    monkeypatch.setenv("TRIAL_START_DATE", "2026-11-20")
    assert sheet_sync.is_configured() is True

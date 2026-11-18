from datetime import datetime, timedelta, timezone

import pytest

import main
from src import ranking, storage
from tests.test_pipeline import approve_all, summarise_all


@pytest.fixture
def pipeline(state_dir, monkeypatch):
    calls = {"fetched": []}
    monkeypatch.setattr(main, "fetch_all_articles", lambda: [dict(item) for item in calls["fetched"]])
    monkeypatch.setattr(main, "classify_news_articles", approve_all)
    monkeypatch.setattr(main, "summarize_articles", summarise_all)
    monkeypatch.setattr(main, "send_telegram_message", lambda text: True)
    return calls


def record_for(url):
    return next(record for record in storage.load_history() if record["url"] == url)


def test_score_breakdown_adds_up_and_names_the_terms(make_article):
    article = make_article("Project reaches financial close on $300 million loan in Vietnam port",
                           description="", trust=1)

    score = ranking.score_breakdown(article)

    assert score["strong"] == 1 and score["deal"] == 1 and score["sector"] == 1 and score["amount"] == 1
    assert score["trust"] == 2
    assert score["total"] == 3 + 2 + 1 + 1 + 2 == ranking.rank_key(article)[0]
    assert {"financial close", "loan", "port"} <= set(score["matched_terms"])


def test_sent_article_keeps_its_trial_data(pipeline, make_article):
    article = make_article()
    pipeline["fetched"] = [article]
    main.run_collect()

    main.run_brief()

    record = record_for(article["url"])
    assert record["status"] == "sent"
    assert record["gemini_decision"] == "accepted"
    assert record["country"] == "Vietnam"
    assert record["news_type"] == "Infrastructure & Project Finance"
    assert record["score"]["total"] > 0
    assert record["shortlist_rank"] == 1
    assert record["brief_rank"] == 1
    assert record["brief_id"].endswith(("-AM", "-PM"))


def test_article_over_the_cap_stays_identifiable_after_it_expires(pipeline, make_article, monkeypatch):
    monkeypatch.setattr(main, "MAX_ARTICLES_PER_BRIEF", 1)
    first = make_article("Sembcorp closes $900 million green loan for Vietnam wind farm")
    second = make_article("Keppel signs port financing with Indian lenders", source="Other")
    pipeline["fetched"] = [first, second]
    main.run_collect()
    main.run_brief()

    state = storage.load_pending()
    left_over = state["articles"][0]
    left_over["published_at"] = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    left_over["first_seen"] = left_over["published_at"]
    storage.save_pending(state)
    main.run_brief()

    record = record_for(left_over["url"])
    assert record["status"] == "expired"
    assert record["gemini_decision"] == "accepted"
    assert record["missed_cap"] is True


def test_code_filter_removals_are_logged_once_with_a_reason(pipeline, make_article):
    pipeline["fetched"] = [
        make_article("3 bank stocks to buy in Singapore now"),
        make_article("Texas utility closes $400 million refinancing", region="GLOBAL", description=""),
        make_article("Singapore opens new museum wing", description=""),
    ]

    main.run_collect()
    main.run_collect()

    reasons = sorted(record["reason"] for record in storage.load_code_rejections())
    assert reasons == ["deny_list", "low_topic_score", "not_apac"]


def test_dry_run_does_not_log_code_filter_removals(pipeline, make_article):
    pipeline["fetched"] = [make_article("3 bank stocks to buy in Singapore now")]

    main.run_collect(dry_run=True)

    assert storage.load_code_rejections() == []


def test_old_code_filter_removals_are_pruned(state_dir):
    now = datetime.now(timezone.utc)
    old = {"hash": "old", "first_seen": (now - timedelta(days=15)).isoformat()}
    recent = {"hash": "recent", "first_seen": (now - timedelta(days=2)).isoformat()}
    storage.save_code_rejections([old, recent])

    storage.add_code_rejections([], now)

    assert [record["hash"] for record in storage.load_code_rejections()] == ["recent"]


def test_local_report_can_read_s3_state(monkeypatch):
    monkeypatch.delenv("AWS_EXECUTION_ENV", raising=False)
    monkeypatch.setenv("S3_BUCKET_NAME", "bucket")
    assert storage._s3_bucket() is None

    monkeypatch.setenv("USE_S3_STATE", "1")
    assert storage._s3_bucket() == "bucket"

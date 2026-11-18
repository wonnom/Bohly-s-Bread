from datetime import datetime, timedelta, timezone

import pytest

import main
from src import storage


def approve_all(batch):
    return [
        {"id": article["id"], "evidence": "Relevant APAC deal.", "should_send": True,
         "country": "Vietnam", "news_type": "Infrastructure & Project Finance"}
        for article in batch
    ]


def summarise_all(batch):
    return {article["id"]: {"summary": "Short note.", "key_figures": [], "key_parties": []} for article in batch}


@pytest.fixture
def pipeline(state_dir, monkeypatch):
    calls = {"fetched": [], "sent": [], "classified": [], "send_ok": True}

    def classify(batch):
        calls["classified"].append(list(batch))
        return approve_all(batch)

    def send(text):
        if calls["send_ok"]:
            calls["sent"].append(text)
        return calls["send_ok"]

    monkeypatch.setattr(main, "fetch_all_articles", lambda: [dict(item) for item in calls["fetched"]])
    monkeypatch.setattr(main, "classify_news_articles", classify)
    monkeypatch.setattr(main, "summarize_articles", summarise_all)
    monkeypatch.setattr(main, "send_telegram_message", send)
    return calls


def statuses():
    return {record["url"]: record["status"] for record in storage.load_history()}


def test_collect_saves_history_without_calling_the_llm(pipeline, make_article):
    article = make_article()
    pipeline["fetched"] = [article]

    main.run_collect()

    assert statuses()[article["url"]] == "pending"
    assert len(storage.load_pending()["articles"]) == 1
    assert pipeline["classified"] == []
    assert pipeline["sent"] == []


def test_repeat_collect_adds_nothing(pipeline, make_article):
    pipeline["fetched"] = [make_article()]

    main.run_collect()
    main.run_collect()

    assert len(storage.load_pending()["articles"]) == 1


def test_brief_sends_once_and_records_the_result(pipeline, make_article):
    article = make_article()
    pipeline["fetched"] = [article]
    main.run_collect()

    main.run_brief()
    main.run_brief()

    assert len(pipeline["sent"]) == 1
    assert statuses()[article["url"]] == "sent"
    assert storage.load_pending()["articles"] == []


def test_sent_article_is_not_collected_again(pipeline, make_article):
    pipeline["fetched"] = [make_article()]
    main.run_collect()
    main.run_brief()

    main.run_collect()

    assert storage.load_pending()["articles"] == []


def test_rejected_articles_keep_their_evidence(pipeline, make_article, monkeypatch):
    article = make_article()
    pipeline["fetched"] = [article]
    monkeypatch.setattr(main, "classify_news_articles", lambda batch: [
        {"id": batch[0]["id"], "evidence": "Minor corporate notice.", "should_send": False}
    ])
    main.run_collect()

    main.run_brief()

    record = storage.load_history()[0]
    assert record["status"] == "rejected"
    assert record["evidence"] == "Minor corporate notice."
    assert pipeline["sent"] == []


def test_dry_runs_do_not_send_or_save(pipeline, make_article):
    pipeline["fetched"] = [make_article()]

    main.run_collect(dry_run=True)
    assert storage.load_history() == []

    main.run_collect()
    before = storage.load_history()
    main.run_brief(dry_run=True)

    assert pipeline["sent"] == []
    assert storage.load_history() == before
    assert len(storage.load_pending()["articles"]) == 1


def test_gemini_failure_keeps_articles_and_next_collect_finishes_the_brief(pipeline, make_article, monkeypatch):
    pipeline["fetched"] = [make_article()]
    main.run_collect()

    def unavailable(batch):
        raise RuntimeError("503 UNAVAILABLE")

    monkeypatch.setattr(main, "classify_news_articles", unavailable)
    result = main.run_brief()

    assert result["statusCode"] == 503
    state = storage.load_pending()
    assert state["brief_due"] is True
    assert len(state["articles"]) == 1

    monkeypatch.setattr(main, "classify_news_articles", approve_all)
    main.run_collect()
    main.run_collect()

    assert len(pipeline["sent"]) == 1
    assert storage.load_pending()["brief_due"] is False


def test_summary_failure_is_retried_without_reclassifying(pipeline, make_article, monkeypatch):
    pipeline["fetched"] = [make_article()]
    main.run_collect()

    def unavailable(batch):
        raise RuntimeError("503 UNAVAILABLE")

    monkeypatch.setattr(main, "summarize_articles", unavailable)
    main.run_brief()
    assert len(pipeline["classified"]) == 1

    monkeypatch.setattr(main, "summarize_articles", summarise_all)
    main.run_collect()

    assert len(pipeline["classified"]) == 1
    assert len(pipeline["sent"]) == 1


def test_failed_telegram_send_stays_eligible_for_the_next_brief(pipeline, make_article):
    article = make_article()
    pipeline["fetched"] = [article]
    main.run_collect()

    pipeline["send_ok"] = False
    main.run_brief()
    assert statuses()[article["url"]] == "accepted"
    assert len(storage.load_pending()["articles"]) == 1

    pipeline["send_ok"] = True
    main.run_brief()
    assert statuses()[article["url"]] == "sent"
    assert len(pipeline["sent"]) == 1


def test_brief_uses_two_batches_of_twenty_and_sends_at_most_twelve(pipeline, make_article):
    countries = ["Vietnam", "India", "Japan", "Korea", "Australia", "Thailand", "Malaysia"]
    sectors = ["solar", "port", "airport", "wind", "grid", "pipeline", "railway"]
    pipeline["fetched"] = [
        make_article(f"{chr(65 + index % 26)}{index}corp {sectors[index % 7]} financing unit{index} {countries[index % 7]} tranche{index * 7}",
                     source=f"Source {index}")
        for index in range(60)
    ]
    main.run_collect()

    main.run_brief()

    assert [len(batch) for batch in pipeline["classified"]] == [20, 20]
    assert sum(record["status"] == "sent" for record in storage.load_history()) == 12


def test_expired_articles_are_dropped_from_the_brief(pipeline, make_article):
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    storage.save_pending({"articles": [make_article(published_at=old)], "brief_due": False})

    main.run_brief()

    assert pipeline["classified"] == []
    assert storage.load_pending()["articles"] == []


def test_same_event_as_a_recent_send_is_not_queued(pipeline, make_article):
    pipeline["fetched"] = [make_article("Amazon to invest $7bn in Indian renewable energy projects by 2030")]
    main.run_collect()
    main.run_brief()

    pipeline["fetched"] = [make_article("Amazon to invest $7bn into Indian renewable projects by 2030")]
    main.run_collect()

    assert storage.load_pending()["articles"] == []


def test_api_keys_are_never_logged(state_dir, monkeypatch, capsys):
    monkeypatch.setattr(main, "NEWSDATA_API", "newsdata-secret")
    monkeypatch.setattr(main, "FINLIGHT_API", "finlight-secret")
    monkeypatch.setattr(main, "fetch_newsdata_articles", lambda queries, key: [])
    monkeypatch.setattr(main, "fetch_finlight_articles", lambda queries, key: [])
    monkeypatch.setattr(main, "fetch_rss_articles", lambda feeds: [])

    main.lambda_handler({"mode": "collect", "dry_run": True}, None)

    output = capsys.readouterr().out
    assert "newsdata-secret" not in output
    assert "finlight-secret" not in output


def test_handler_rejects_unknown_modes(state_dir):
    assert main.lambda_handler({"mode": "nonsense"}, None)["statusCode"] == 400

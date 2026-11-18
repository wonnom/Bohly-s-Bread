from datetime import datetime, timedelta, timezone

from src import ranking, storage


def test_tracking_parameters_do_not_create_a_new_url():
    plain = "https://example.com/story?id=7"
    tracked = "https://EXAMPLE.com/story?utm_source=rss&id=7&fbclid=abc#top"
    assert storage.hash_url(plain) == storage.hash_url(tracked)


def test_existing_history_hashes_still_match():
    import hashlib

    url = "https://www.reuters.com/business/energy/asian-gasoline-margin-2026-10-30/"
    assert storage.hash_url(url) == hashlib.sha256(url.lower().encode()).hexdigest()


def test_same_story_from_three_outlets_is_one_event():
    titles = [
        "Amazon to invest $7bn in Indian renewable energy projects by 2030",
        "Amazon to invest $7bn into Indian renewable projects by 2030",
        "Amazon to invest $7 billion in renewable energy in India by 2030",
    ]
    assert ranking.same_event(titles[0], titles[1])
    assert ranking.same_event(titles[0], titles[2])
    assert ranking.same_event(titles[1], titles[2])


def test_same_amount_and_place_with_different_wording_is_one_event():
    assert ranking.same_event(
        "Amazon to invest $7 billion in renewable energy in India by 2030",
        "Amazon plans US$7 billion investment in Indian solar, wind projects by 2030",
    )


def test_same_amount_in_different_countries_is_not_merged():
    assert not ranking.same_event(
        "Sembcorp signs $300 million solar financing in Vietnam",
        "Sembcorp signs $300 million solar financing in Indonesia",
    )


def test_different_deals_are_not_merged():
    assert not ranking.same_event(
        "ADB approves $500 million loan for Pakistan pension reforms",
        "ADB approves $500 million loan for Bangladesh solar storage",
    )


def test_same_event_keeps_the_most_trusted_source(make_article):
    weak = make_article("Amazon to invest $7bn into Indian renewable projects by 2030", trust=3)
    strong = make_article("Amazon to invest $7bn in Indian renewable energy projects by 2030", trust=1)

    kept, dropped = ranking.drop_same_event([weak, strong])

    assert kept == [strong]
    assert dropped == [weak]


def test_story_matching_a_recent_send_is_dropped(make_article):
    article = make_article("Amazon to invest $7bn into Indian renewable projects by 2030")
    kept, dropped = ranking.drop_same_event(
        [article], recent_titles=["Amazon to invest $7bn in Indian renewable energy projects by 2030"]
    )
    assert kept == []
    assert dropped == [article]


def test_code_filter_keeps_apac_deals(make_article):
    assert ranking.passes_code_filter(make_article("RENOVA secures JPY 10.34 billion green loan from Japanese banks"))


def test_code_filter_rejects_non_apac_stories_from_global_sources(make_article):
    article = make_article("Texas utility closes $400 million refinancing", region="GLOBAL", description="")
    assert not ranking.passes_code_filter(article)


def test_global_source_passes_when_the_story_mentions_apac(make_article):
    article = make_article("Woodside seals LNG supply deal for Japan", region="GLOBAL", description="")
    assert ranking.passes_code_filter(article)


def test_code_filter_rejects_notices_and_stock_tips(make_article):
    for title in (
        "Sea Q3 2026 earnings call transcript",
        "3 bank stocks to buy in Singapore now",
        "Kulicke & Soffa schedules fourth quarter conference call",
    ):
        assert not ranking.passes_code_filter(make_article(title))


def test_code_filter_rejects_off_topic_apac_news(make_article):
    assert not ranking.passes_code_filter(
        make_article("Singapore seizes cruise ship after dispute", description="A vessel was held in port waters.")
        | {"description": ""}
    )


def test_freshness_window(make_article):
    now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    fresh = make_article(published_at=(now - timedelta(hours=23)).isoformat())
    stale = make_article(published_at=(now - timedelta(hours=25)).isoformat())
    assert ranking.is_fresh(fresh, now)
    assert not ranking.is_fresh(stale, now)


def test_undated_articles_age_from_when_they_were_first_seen(make_article):
    now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    article = make_article(published_at="", first_seen=(now - timedelta(hours=2)).isoformat())
    assert ranking.is_fresh(article, now)


def test_shortlist_respects_total_and_per_source_caps(make_article):
    articles = [
        make_article(f"Solar loan number {index} for India", source="Busy Source") for index in range(12)
    ] + [make_article(f"Port financing number {index} for Vietnam", source=f"Source {index}") for index in range(50)]

    shortlisted = ranking.shortlist(articles, limit=40, per_source=8)

    assert len(shortlisted) == 40
    assert sum(article["source"] == "Busy Source" for article in shortlisted) <= 8


def test_stronger_deal_signals_rank_first(make_article):
    weak = make_article("India solar capacity grows", description="")
    strong = make_article("Project reaches financial close on $300 million syndicated loan in Vietnam", description="")
    assert ranking.shortlist([weak, strong], limit=2)[0] is strong

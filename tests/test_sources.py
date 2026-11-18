import io
import json
from pathlib import Path
from types import SimpleNamespace

from src import search_sources
from src.feeds import Feed

FIXTURES = Path(__file__).parent / "fixtures"
FEED = Feed("Sample", "https://example.com/feed", "energy", "APAC", 2)


def test_rss_parsing_cleans_text_and_keeps_publish_date():
    articles = search_sources.parse_rss_feed((FIXTURES / "sample_feed.xml").read_bytes(), FEED)

    assert articles[0]["description"] == "The Asian Development Bank approved financing for 1,000 MW of solar."
    assert articles[0]["published_at"] == "2026-11-18T02:00:00+00:00"
    assert articles[0]["provider"] == "rss"
    assert articles[0]["region"] == "APAC"


def test_google_news_items_use_the_real_publisher():
    google_feed = Feed("Google", "https://news.google.com/rss/search?q=x", "deals", "APAC", 2)
    article = search_sources.parse_rss_feed((FIXTURES / "sample_feed.xml").read_bytes(), google_feed)[1]

    assert article["title"] == "China Gas doubles volume in LNG deal"
    assert article["source"] == "Reuters"
    assert article["trust"] == 1
    assert article["description"] == ""


def test_one_failing_feed_does_not_stop_the_others(monkeypatch):
    good = Feed("Good", "https://example.com/good", "energy", "APAC", 2)
    bad = Feed("Bad", "https://example.com/bad", "energy", "APAC", 2)

    def fake_download(url):
        if url.endswith("bad"):
            raise OSError("feed down")
        return (FIXTURES / "sample_feed.xml").read_bytes()

    monkeypatch.setattr(search_sources, "_download", fake_download)
    assert len(search_sources.fetch_rss_articles([good, bad])) == 2


def test_descriptions_are_capped_at_50_words():
    article = search_sources.normalize_article(
        {"title": "T", "link": "https://example.com", "description": "word " * 80}, provider="newsdata"
    )
    assert len(article["description"].split()) == 50


def test_newsdata_searches_metadata_and_keeps_every_query(monkeypatch):
    requested = []

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        requested.append(request.full_url)
        index = len(requested)
        body = {"results": [{"title": f"Story {index}", "link": f"https://example.com/{index}",
                             "source_name": "Example", "pubDate": "2026-11-18 02:00:00"}]}
        return FakeResponse(json.dumps(body).encode())

    monkeypatch.setattr(search_sources.urllib.request, "urlopen", fake_urlopen)
    articles = search_sources.fetch_newsdata_articles(['"project finance" OR LNG', "commodities"], "secret")

    assert len(articles) == 2
    assert all("qInMeta=" in url and "qInTitle" not in url for url in requested)
    assert articles[0]["published_at"] == "2026-11-18T02:00:00+00:00"


def test_finlight_keeps_results_from_every_query_and_targets_apac(monkeypatch):
    calls = []

    class FakeArticles:
        def fetch_articles(self, params):
            calls.append(params)
            index = len(calls)
            return SimpleNamespace(articles=[{"title": f"Story {index}", "link": f"https://example.com/{index}",
                                              "source": "www.reuters.com", "summary": "Text"}])

    monkeypatch.setattr(search_sources, "FinlightApi", lambda config: SimpleNamespace(articles=FakeArticles()))
    monkeypatch.setattr(search_sources, "ApiConfig", lambda api_key: None)
    monkeypatch.setattr(search_sources, "GetArticlesParams", lambda **kwargs: SimpleNamespace(**kwargs))

    articles = search_sources.fetch_finlight_articles(["first", "second"], "secret")

    assert [article["title"] for article in articles] == ["Story 1", "Story 2"]
    assert {"SG", "IN", "CN", "AU"} <= set(calls[0].countries)
    assert "markets" not in calls[0].categories
    assert {"finance.yahoo.com", "seekingalpha.com", "www.benzinga.com"} <= set(calls[0].excludeSources)

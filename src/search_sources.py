"""Fetch articles from NewsData, Finlight and RSS feeds into one common shape."""
import html
import json
import re
import urllib.parse
import urllib.request
from calendar import timegm
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import feedparser

from src.feeds import FINLIGHT_CATEGORIES, FINLIGHT_COUNTRIES, FINLIGHT_EXCLUDED_SOURCES, Feed

try:
    from finlight_client import ApiConfig, FinlightApi
    from finlight_client.models import GetArticlesParams
except ImportError:  # pragma: no cover - optional dependency
    FinlightApi = None
    ApiConfig = None
    GetArticlesParams = None

MAX_DESCRIPTION_WORDS = 50
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "Mozilla/5.0 (compatible; news-brief-bot/1.0)"
TIER_ONE_PUBLISHERS = (
    "reuters", "bloomberg", "financial times", "ft.com", "cnbc", "s&p global", "spglobal",
    "wall street journal", "wsj", "nikkei",
)


def _clean_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    return " ".join(text.split())


def _cap_words(text: str) -> str:
    return " ".join(text.split()[:MAX_DESCRIPTION_WORDS])


def _trust_for(source: str, default: int) -> int:
    lowered = source.lower()
    return 1 if any(name in lowered for name in TIER_ONE_PUBLISHERS) else default


def _iso_time(value: Any) -> str:
    """Convert provider dates to ISO 8601 in UTC. Dates without a timezone are treated as UTC."""
    if not value:
        return ""
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def normalize_article(
    raw: Any, provider: str, region: str = "GLOBAL", trust: int = 3
) -> Dict[str, Any]:
    """Convert a provider payload into the fields used by the pipeline."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        if not hasattr(raw, "__dict__"):
            return {}
        raw = {key: value for key, value in vars(raw).items() if not key.startswith("_")}

    source = str(raw.get("source_name") or raw.get("source") or raw.get("publisher") or "").strip()
    description = _clean_text(raw.get("description") or raw.get("summary") or raw.get("snippet") or "")
    if description.upper().startswith("ONLY AVAILABLE"):
        description = ""

    return {
        "title": _clean_text(raw.get("title")),
        "url": str(raw.get("link") or raw.get("url") or raw.get("article_url") or "").strip(),
        "source": source,
        "description": _cap_words(description),
        "published_at": _iso_time(raw.get("pubDate") or raw.get("publishDate") or raw.get("published_at")),
        "provider": provider,
        "feed": provider,
        "region": region,
        "trust": _trust_for(source, trust),
    }


def fetch_newsdata_articles(queries: Iterable[str], api_key: str) -> List[Dict[str, Any]]:
    """Search NewsData's title, description, keywords and URL fields for each query."""
    if not api_key:
        return []

    results: List[Dict[str, Any]] = []
    for query in queries:
        params = urllib.parse.urlencode({"apikey": api_key, "qInMeta": query, "language": "en"})
        request = urllib.request.Request(
            f"https://newsdata.io/api/1/latest?{params}", headers={"User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - network failure path
            print(f"NewsData fetch failed: {type(exc).__name__}: {getattr(exc, 'code', '')}")
            continue

        for item in payload.get("results") or []:
            article = normalize_article(item, provider="newsdata")
            if article.get("title") and article.get("url"):
                results.append(article)
    return results


def fetch_finlight_articles(queries: Iterable[str], api_key: str) -> List[Dict[str, Any]]:
    """Search Finlight for articles about APAC countries, excluding retail stock sites."""
    if not api_key or FinlightApi is None or ApiConfig is None or GetArticlesParams is None:
        return []

    client = FinlightApi(config=ApiConfig(api_key=api_key))
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    results: List[Dict[str, Any]] = []

    for query in queries:
        params = GetArticlesParams(
            query=query,
            countries=FINLIGHT_COUNTRIES,
            categories=FINLIGHT_CATEGORIES,
            excludeSources=FINLIGHT_EXCLUDED_SOURCES,
            language="en",
            from_=since,
            pageSize=50,
        )
        try:
            response = client.articles.fetch_articles(params=params)
        except Exception as exc:  # pragma: no cover - network failure path
            print(f"Finlight fetch failed: {type(exc).__name__}")
            continue

        raw_articles = response.get("articles", []) if isinstance(response, dict) else getattr(response, "articles", [])
        for item in raw_articles or []:
            article = normalize_article(item, provider="finlight", region="APAC", trust=2)
            if article.get("title") and article.get("url"):
                results.append(article)
    return results


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read()


def _entry_time(entry: Any) -> str:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return ""
    return datetime.fromtimestamp(timegm(parsed), tz=timezone.utc).isoformat()


def parse_rss_feed(content: bytes, feed: Feed) -> List[Dict[str, Any]]:
    """Parse RSS or Atom content into articles."""
    is_google_news = "news.google.com" in feed.url
    articles: List[Dict[str, Any]] = []

    for entry in feedparser.parse(content).entries:
        title = _clean_text(entry.get("title"))
        url = str(entry.get("link") or "").strip()
        if not title or not url:
            continue

        source: Optional[str] = feed.name
        description = _cap_words(_clean_text(entry.get("summary") or entry.get("description")))
        if is_google_news:
            publisher = _clean_text((entry.get("source") or {}).get("title"))
            source = publisher or feed.name
            if publisher and title.endswith(f" - {publisher}"):
                title = title[: -len(f" - {publisher}")].strip()
            description = ""  # Google News descriptions only repeat the headline.

        articles.append({
            "title": title,
            "url": url,
            "source": source,
            "description": description,
            "published_at": _entry_time(entry),
            "provider": "rss",
            "feed": feed.name,
            "region": feed.region,
            "trust": _trust_for(source, feed.trust),
            "category": feed.category,
        })
    return articles


def _fetch_feed(feed: Feed) -> List[Dict[str, Any]]:
    try:
        return parse_rss_feed(_download(feed.url), feed)
    except Exception as exc:
        print(f"RSS fetch failed for {feed.name}: {type(exc).__name__}: {getattr(exc, 'code', '')}")
        return []


def fetch_rss_articles(feeds: Iterable[Feed]) -> List[Dict[str, Any]]:
    """Fetch every feed concurrently. A failing feed never stops the others."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        return [article for articles in pool.map(_fetch_feed, list(feeds)) for article in articles]

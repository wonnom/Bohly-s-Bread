import json
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List

try:
    from finlight_client import FinlightApi, ApiConfig
    from finlight_client.models import GetArticlesParams
except ImportError:  # pragma: no cover - optional dependency
    FinlightApi = None
    ApiConfig = None
    GetArticlesParams = None


def normalize_article(raw: Dict[str, Any], source_name_hint: str = "newsdata") -> Dict[str, Any]:
    """Normalize provider-specific article payloads to the fields used by the LLM pipeline."""
    if raw is None:
        return {}

    if not isinstance(raw, dict):
        if hasattr(raw, "__dict__"):
            raw = {k: v for k, v in vars(raw).items() if not k.startswith("_")}
        else:
            return {}

    title = (raw.get("title") or "").strip()
    link = (raw.get("link") or raw.get("url") or raw.get("article_url") or "").strip()
    source_name = (
        raw.get("source_name")
        or raw.get("source")
        or raw.get("publisher")
        or raw.get("site")
        or ""
    ).strip()
    description = (
        raw.get("description")
        or raw.get("summary")
        or raw.get("snippet")
        or raw.get("content")
        or ""
    ).strip()

    if source_name_hint == "newsdata":
        description = " ".join(description.split()[:50])

    return {
        "title": title,
        "source": source_name,
        "source_name": source_name,
        "description": description,
        "url": link,
        "link": link,
    }


def fetch_newsdata_articles(topics: Iterable[str], api_key: str) -> List[Dict[str, Any]]:
    """Fetch NewsData articles for each topic and normalize the article payloads."""
    if not api_key:
        print("No newsdata api key found")
        return []

    results: List[Dict[str, Any]] = []
    cleaned_topics = [topic.strip() for topic in topics if str(topic).strip()]

    for topic in cleaned_topics:
        query = urllib.parse.quote_plus(topic)
        url = (
            f"https://newsdata.io/api/1/latest?apikey={api_key}"
            f"&qInTitle={query}&country=sg&language=en"
        )

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - network failure path
            print(f"NewsData fetch failed for '{topic}': {exc}")
            continue
        print("num of articles from newsdata: ", len(payload.get("results", [])), "\n")
        for item in payload.get("results", []):
            normalized = normalize_article(item, source_name_hint="newsdata")
            if normalized.get("title") or normalized.get("url"):
                results.append(normalized)

    return results


def fetch_finlight_articles(topics: Iterable[str], api_key: str) -> List[Dict[str, Any]]:
    """Fetch Finlight articles using the configured categories and normalize to the same schema."""
    if not api_key or FinlightApi is None or ApiConfig is None or GetArticlesParams is None:
        print("no finlightapi key or dependencies not downloaded")
        return []
    cleaned_topics = [topic.strip() for topic in topics if str(topic).strip()]
    
    client = FinlightApi(config=ApiConfig(api_key=api_key))
    for topic in cleaned_topics:
        params = GetArticlesParams(
            query=topic,
            countries=["SG"],
            categories=["markets", "economy", "business", "regulation", "energy", "commodities", "climate"],
        )

        try:
            response = client.articles.fetch_articles(params=params)
        except Exception as exc:  # pragma: no cover - network failure path
            print(f"Finlight fetch failed: {exc}")
            return []

        raw_articles = []
        if hasattr(response, "articles"):
            raw_articles = response.articles
            
        elif isinstance(response, dict):
            raw_articles = response.get("articles", [])

        print("num of articles from finlight: ", len(raw_articles), "\n")
        results: List[Dict[str, Any]] = []

        for item in raw_articles:
            normalized = normalize_article(item, source_name_hint="finlight")
            if normalized.get("title") or normalized.get("url"):
                results.append(normalized)

    return results

import os
from dotenv import load_dotenv

if __name__ == "__main__":
    load_dotenv()
    TOPICS_RAW = os.environ.get("TOPICS", "central bank digital currencies")
    TOPICS = [topic.strip() for topic in str(TOPICS_RAW).split(",") if topic.strip()]
    NEWSDATA_API = os.environ.get("NEWSDATA_API_KEY", "")
    FINLIGHT_API = os.environ.get("FINLIGHT_API_KEY", "")
    res = fetch_finlight_articles(TOPICS, FINLIGHT_API)
    fetch_newsdata_articles(TOPICS, NEWSDATA_API)
    print(res)
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

SYSTEM_RULES = """
You are a senior investment banking analyst. Evaluate the following news headlines and summaries.
Return a JSON array of the articles that represent major APAC infrastructure deals, significant M&A, or major macroeconomic shifts.
Strictly exclude minor corporate announcements, retail stock-picking, and news not related to Asia Pacific.

INCLUDE:
- actual transactions
- financing announcements
- market-moving developments
- significant policy decisions
- major macro or regulatory shifts

EXCLUDE:
- generic commentary or opinion pieces
- articles with little financial or commercial impact

Use the article title, a short description snippet, and the source name to decide.

If an article should be sent, include both the country and the type of news it falls under.
If an article should not be sent, set "should_send" to false and omit the "country" and "news_type" fields.

Return JSON only in this exact schema:
{
  "results": [
    {
      "title": "Article title",
      "source": "Reuters",
      "should_send": true,
      "country": "Singapore",
      "news_type": "Infrastructure"
    }
  ]
}

"""

SUMMARY_SYSTEM_RULES = """
You are an investment banker writing a concise briefing note.
For each article, write a summary in this exact format:

1. Factual summary: 2 sentences that state only what happened, explaining the concise summary of the development. Key figures, including transaction value, financing amount, capacity, production volumes or prices where available.
2. Analysis: 2 sentences on some analysis about it, for someone interested to work in banking and finance. Why the development matters and its potential implications for the banking, infrastructure or energy sectors.

Do not speculate beyond the article. Keep it brief and professional.

Return JSON only in this exact schema:
{
  "results": [
    {
      "title": "Article title",
      "summary": "Factual summary and analysis"
    }
  ]
}
"""


def _generate_gemini_json(prompt: str) -> Dict[str, Any]:
    """Send a prompt to Gemini, retry once after a short delay, and return the parsed JSON."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    last_error = None

    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            raw_text = (response.text or "").strip()

            if raw_text.startswith("```"):
                raw_text = raw_text.strip("`")
                if raw_text.lower().startswith("json"):
                    raw_text = raw_text[4:].strip()

            try:
                return json.loads(raw_text)
            except json.JSONDecodeError:
                start = raw_text.find("{")
                end = raw_text.rfind("}")
                if start == -1 or end == -1:
                    raise ValueError(f"Unable to parse Gemini response: {raw_text}")
                return json.loads(raw_text[start:end + 1])
        except Exception as exc:
            last_error = exc
            print(f"Gemini call failed on attempt {attempt + 1}: {exc}")
            if attempt == 0:
                print("Retrying in 10 seconds...")
                time.sleep(10)

    raise RuntimeError(f"Gemini call failed after retry: {last_error}")


def classify_news_articles(
    articles: List[Dict[str, Any]],
    hash_fn: Optional[Callable[[str], str]] = None,
    status_writer: Optional[Callable[[str, str], None]] = None,
) -> List[Dict[str, Any]]:
    """Classify a batch of articles at once and return the decisions for each one."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it to your .env file.")

    if not isinstance(articles, list):
        articles = [articles]

    if not articles:
        return []

    formatted_articles = []
    title_to_url: Dict[str, str] = {}

    for article in articles:
        title = (article.get("title") or "").strip()
        url = (article.get("url") or article.get("link") or "").strip()
        source = (article.get("source_name") or article.get("source") or article.get("publisher") or article.get("site") or "").strip()
        description = (article.get("description") or article.get("snippet") or article.get("content") or "").strip()

        if not title and not url:
            continue

        if title and url:
            title_to_url[title.strip().lower()] = url

        formatted_articles.append({
            "title": title,
            "source": source,
            "description": description,
            "url": url,
            "hash": hash_fn(url) if hash_fn and url else "",
        })

    prompt_parts = [SYSTEM_RULES]
    prompt_parts.append("Classify each article in the batch below. Only return JSON in the required schema.")

    for idx, item in enumerate(formatted_articles, start=1):
        source_line = f"Source: {item['source']}\n" if item.get("source") else ""
        description_line = f"Description: {item['description']}\n" if item.get("description") else ""
        prompt_parts.append(
            f"\nArticle {idx}\n"
            f"Title: {item['title']}\n"
            f"{source_line}"
            f"{description_line}"
        )

    prompt = "\n".join(prompt_parts)
    print(prompt)

    parsed = _generate_gemini_json(prompt)
    print("Baker 1 filter done\n")

    results = parsed.get("results", [])
    final_results: List[Dict[str, Any]] = []

    for item in results:
        title = (item.get("title") or "").strip()
        source = (item.get("source") or "").strip()
        matched_url = title_to_url.get(title.lower()) if title else ""
        should_send = bool(item.get("should_send", False))

        decision = {
            "title": title,
            "source": source,
            "url": matched_url,
            "should_send": should_send,
        }

        if should_send:
            country = (item.get("country") or "").strip()
            news_type = (item.get("news_type") or "").strip()
            if country:
                decision["country"] = country
            if news_type:
                decision["news_type"] = news_type

        if hash_fn and matched_url:
            decision["hash"] = hash_fn(matched_url)

        if status_writer and matched_url:
            status_writer(matched_url, "accepted" if should_send else "rejected")

        final_results.append(decision)

    return final_results


def summarize_articles(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Summarize only the accepted articles in a second pass using an investment-banker lens."""
    if not articles:
        return []

    prompt_parts = [SUMMARY_SYSTEM_RULES]
    prompt_parts.append("Write the summary for each accepted article below. Only return JSON in the required schema.")

    for idx, item in enumerate(articles, start=1):
        title = (item.get("title") or "").strip()
        source = (item.get("source_name") or item.get("source") or "").strip()
        description = (item.get("description") or item.get("snippet") or item.get("content") or "").strip()
        prompt_parts.append(
            f"\nArticle {idx}\n"
            f"Title: {title}\n"
            f"Source: {source}\n"
            f"Description: {description}\n"
        )

    parsed = _generate_gemini_json("\n".join(prompt_parts))
    results = parsed.get("results", [])
    finalized: List[Dict[str, Any]] = []

    for item in results:
        finalized.append({
            "title": (item.get("title") or "").strip(),
            "summary": (item.get("summary") or "").strip(),
        })

    return finalized


if __name__ == "__main__":
    sample_articles = [
        {
            "title": "Central bank approves $2.4B sovereign bond issuance program",
            "url": "https://example.com/article-1",
            "snippet": "The government said the financing will support infrastructure and debt refinancing across major cities.",
        },
        {
            "title": "Company says it has hired a new marketing consultant",
            "url": "https://example.com/article-2",
            "snippet": "The firm said the contract will help with branding and awareness across social channels.",
        },
    ]
    print(json.dumps(classify_news_articles(sample_articles), indent=2))


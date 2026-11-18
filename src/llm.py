import json
import os
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_BACKUP_MODEL = os.getenv("GEMINI_BACKUP_MODEL", "gemini-3.5-flash")
GEMINI_TIMEOUT_MS = 60_000

SYSTEM_RULES = """
You are a senior project finance analyst screening news for a daily Asia-Pacific briefing.
Decide whether each article belongs in the briefing, using only its title, source and description.

SEND articles about Asia-Pacific (including Singapore, Southeast Asia, China, India, Japan, Korea and Australia) covering:
1. Infrastructure and project finance: deals, financing, refinancing, M&A and investments in power, renewables,
   utilities, transport, ports, airports, data centres and telecoms, including loans, bonds, private credit and PPPs.
2. Banking and financial markets: significant lending, syndicated loans, bank strategy or M&A, private credit,
   trade finance, cross-border payments, interest rates and monetary policy.
3. Energy production and demand: oil, gas, LNG, electricity, renewables and storage projects, capacity and investment.
4. Energy and commodity markets: price moves, supply disruptions, sanctions, OPEC+ decisions, LNG flows and shortages
   that materially affect Asia-Pacific.

DO NOT SEND: generic commentary or opinion, retail stock tips, minor corporate announcements, routine notices,
or news with no clear Asia-Pacific relevance.

For every article, return:
- id: the article's number, copied exactly.
- evidence: one sentence of at most 25 words, based only on the supplied text, stating why it passes or fails.
- should_send: true or false, consistent with the evidence.
- country: the main Asia-Pacific country, only when should_send is true.
- news_type: one of "Infrastructure & Project Finance", "Banking & Financial Markets", "Energy Production & Demand",
  or "Energy & Commodity Markets", only when should_send is true.
"""

SUMMARY_SYSTEM_RULES = """
You are a banker writing a short note that helps a reader decide whether to open the article.
For each article, using only the supplied title and description, return:
- id: the article's number, copied exactly.
- summary: one sentence on what happened, then one sentence on why it matters for banking, infrastructure or energy.
- key_figures: values stated in the text, such as deal size, capacity or price. Use an empty list if none are stated.
- key_parties: banks, borrowers, sponsors, governments or investors named in the text. Use an empty list if none are named.

Never add facts, figures or names that are not in the supplied text.
"""

FILTER_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "results": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "id": types.Schema(type=types.Type.INTEGER),
                    "evidence": types.Schema(type=types.Type.STRING),
                    "should_send": types.Schema(type=types.Type.BOOLEAN),
                    "country": types.Schema(type=types.Type.STRING),
                    "news_type": types.Schema(type=types.Type.STRING),
                },
                required=["id", "evidence", "should_send"],
                property_ordering=["id", "evidence", "should_send", "country", "news_type"],
            ),
        )
    },
    required=["results"],
)

SUMMARY_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "results": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "id": types.Schema(type=types.Type.INTEGER),
                    "summary": types.Schema(type=types.Type.STRING),
                    "key_figures": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
                    "key_parties": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
                },
                required=["id", "summary", "key_figures", "key_parties"],
                property_ordering=["id", "summary", "key_figures", "key_parties"],
            ),
        )
    },
    required=["results"],
)


def _generate_gemini_json(prompt: str, schema: Optional[types.Schema] = None) -> Dict[str, Any]:
    """Send a prompt to Gemini, retry once on the backup model, and return the parsed JSON."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it to your .env file.")

    client = genai.Client(api_key=GEMINI_API_KEY, http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS))
    last_error = None

    for attempt in range(2):
        model = GEMINI_MODEL if attempt == 0 else GEMINI_BACKUP_MODEL
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
            return json.loads((response.text or "").strip())
        except Exception as exc:
            last_error = exc
            print(f"Gemini call failed on attempt {attempt + 1}: {exc}")
            if attempt == 0:
                print("Retrying in 10 seconds...")
                time.sleep(10)

    raise RuntimeError(f"Gemini call failed after retry: {last_error}")


def _article_block(article: Dict[str, Any]) -> str:
    return (
        f"\nArticle {article['id']}\n"
        f"Title: {article.get('title', '')}\n"
        f"Source: {article.get('source', '')}\n"
        f"Description: {article.get('description', '')}\n"
    )


def classify_news_articles(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return one decision per recognised article ID. Articles left out stay undecided."""
    if not articles:
        return []

    prompt = SYSTEM_RULES + "".join(_article_block(article) for article in articles)
    parsed = _generate_gemini_json(prompt, FILTER_SCHEMA)
    valid_ids = {article["id"] for article in articles}

    decisions: List[Dict[str, Any]] = []
    for item in parsed.get("results", []):
        if item.get("id") not in valid_ids:
            continue
        should_send = item.get("should_send") is True
        decision = {
            "id": item["id"],
            "evidence": str(item.get("evidence") or "").strip(),
            "should_send": should_send,
        }
        if should_send:
            decision["country"] = str(item.get("country") or "").strip()
            decision["news_type"] = str(item.get("news_type") or "").strip()
        decisions.append(decision)
    return decisions


def summarize_articles(articles: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Return short summaries for the selected articles, keyed by article ID."""
    if not articles:
        return {}

    prompt = SUMMARY_SYSTEM_RULES + "".join(_article_block(article) for article in articles)
    parsed = _generate_gemini_json(prompt, SUMMARY_SCHEMA)
    valid_ids = {article["id"] for article in articles}

    return {
        item["id"]: {
            "summary": str(item.get("summary") or "").strip(),
            "key_figures": [str(value).strip() for value in item.get("key_figures") or [] if str(value).strip()],
            "key_parties": [str(value).strip() for value in item.get("key_parties") or [] if str(value).strip()],
        }
        for item in parsed.get("results", [])
        if item.get("id") in valid_ids
    }

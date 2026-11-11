import os
import json
import hashlib
import urllib.request
from pathlib import Path
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

from src.llm import classify_news_articles, summarize_articles
from src.search_sources import fetch_finlight_articles, fetch_newsdata_articles

# Fallbacks allow local testing if environment variables aren't set yet
if os.path.exists(".env"):
    load_dotenv()

IS_LAMBDA = os.environ.get("AWS_EXECUTION_ENV") is not None
# ---------------------------------------------------------
# 3. Environment Variables (Works locally & in AWS)
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_LOCAL_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_LOCAL_CHAT_ID")
TOPICS_RAW = os.environ.get("TOPICS", "central bank digital currencies")
TOPICS = [topic.strip() for topic in str(TOPICS_RAW).split(",") if topic.strip()]
NEWSDATA_API = os.environ.get("NEWSDATA_API_KEY", "")
FINLIGHT_API = os.environ.get("FINLIGHT_API_KEY", "")

# ---------------------------------------------------------
# 4. Storage Setup (Local file vs S3 Client)
# ---------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent / "data"
SEEN_URLS_FILE = DATA_DIR / "seen_urls.json"

STATE_FILE_KEY = 'seen_urls.json'
BUCKET_NAME = os.environ.get('S3_BUCKET_NAME')

# Only instantiate S3 client if running inside Lambda and an S3 bucket is configured.
s3 = boto3.client('s3') if IS_LAMBDA and BUCKET_NAME else None


def _read_local_seen_urls_file() -> list[dict]:
    """Read the local JSON cache used when S3 is unavailable."""
    try:
        with SEEN_URLS_FILE.open('r', encoding='utf-8') as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []

    if isinstance(payload, dict):
        records = payload.get("seen_hashes", [])
    elif isinstance(payload, list):
        records = payload
    else:
        records = []

    return [item for item in records if isinstance(item, dict)]


def _write_local_seen_urls_file(records: list[dict]):
    """Persist the local cache on disk when S3 is not configured."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"seen_hashes": records}
    with SEEN_URLS_FILE.open('w', encoding='utf-8') as fh:
        json.dump(payload, fh, indent=2)


def hash_url(url: str) -> str:
    """Return a stable hash for a URL."""
    cleaned = (url or "").strip().lower()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()

def load_seen_urls() -> list[dict]:
    """Fetch stored URL records from S3 when configured, otherwise use the local cache."""
    if s3 is not None and BUCKET_NAME:
        try:
            response = s3.get_object(Bucket=BUCKET_NAME, Key=STATE_FILE_KEY)
            payload = json.loads(response['Body'].read().decode('utf-8'))
            if isinstance(payload, dict):
                records = payload.get("seen_hashes", [])
            elif isinstance(payload, list):
                records = payload
            else:
                records = []
            return [item for item in records if isinstance(item, dict)]
        except ClientError as e:
            # If the file does not exist yet (first run), return an empty list
            if e.response['Error']['Code'] == 'NoSuchKey':
                return []
            raise e

    return _read_local_seen_urls_file()


def save_seen_urls(records: list[dict]):
    """Persist updated URL history back to S3 or the local cache when not in AWS."""
    payload = {"seen_hashes": records}

    if s3 is not None and BUCKET_NAME:
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=STATE_FILE_KEY,
            Body=json.dumps(payload, indent=2).encode("utf-8"),
            ContentType="application/json"
        )
        return

    _write_local_seen_urls_file(records)

def mark_url_status_in_memory(url: str, status: str) -> list[dict]:
    """Update the URL cache status using the callback signature used by the scraper."""
    cleaned_url = (url or "").strip()
    if not cleaned_url:
        return []

    hash_value = hash_url(cleaned_url)
    record = {"url": cleaned_url, "hash": hash_value, "status": status}
    return [record]

def is_url_new_in_memory(records: list[dict], url: str) -> bool:
    """Check against the in-memory list without disk/S3 calls."""
    cleaned = (url or "").strip()
    if not cleaned:
        return False
    seen_hashes = {record.get("hash") for record in records if record.get("hash")}
    return hash_url(cleaned) not in seen_hashes


def send_telegram_message(text: str):
    """Sends a synchronous message to Telegram using standard urllib."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}).encode('utf-8')

    req = urllib.request.Request(
        url,
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req) as response:
            return response.read()
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")
        return None


def extract_article_fields(item):
    """Normalize either XML tag objects or JSON dict results into a common article shape."""
    if isinstance(item, dict):
        url = (item.get("link") or item.get("url") or "").strip()
        source_name = ""
        source = item.get("source_name") or item.get("source") or {}
        if isinstance(source, dict):
            source_name = (source.get("name") or source.get("title") or source.get("source_id") or "").strip()
        else:
            source_name = str(source).strip()
        description = item.get("description") or item.get("snippet") or item.get("content") or ""
        return {
            "title": (item.get("title") or "").strip(),
            "source": source_name,
            "source_name": source_name,
            "description": description,
            "url": url,
            "link": url,
        }

    link = item.find("link")
    url = (link.text if link is not None else "").strip()
    source_tag = item.find("source")
    source_name = source_tag.text.strip() if source_tag is not None and source_tag.text else ""
    title = item.title.text.strip() if item.title is not None else ""
    description_tag = item.find("description")
    description = description_tag.text.strip() if description_tag is not None and description_tag.text else ""
    return {
        "title": title,
        "source": source_name,
        "source_name": source_name,
        "description": description,
        "url": url,
        "link": url,
    }


def deduplicate(items):
    """Return only URLs not already in the saved cache or repeated in this batch."""
    seen_records = load_seen_urls()
    seen_hashes = {record.get("hash") for record in seen_records if record.get("hash")}
    unique_items = []

    for item in items:
        article = extract_article_fields(item)
        url = article.get("url", "").strip()
        if not url:
            continue

        key = hash_url(url)
        if key in seen_hashes:
            continue

        unique_items.append(article)
        seen_hashes.add(key)

    return unique_items, seen_hashes


def lambda_handler(event, context):
    """Core AWS Lambda function entry point."""
    all_articles = []

    if NEWSDATA_API:
        print("newsdata api present: ",  NEWSDATA_API)
        all_articles.extend(fetch_newsdata_articles(TOPICS, NEWSDATA_API))
    if FINLIGHT_API:
        print("finlight api present: ",  FINLIGHT_API)
        all_articles.extend(fetch_finlight_articles(TOPICS, FINLIGHT_API))

    if not all_articles:
        return {"statusCode": 500, "body": json.dumps(f"No news articles found for configured topics {TOPICS}")}

    articles, _ = deduplicate(all_articles)
    print("Search complete, deduped length = ", len(articles))

    batch_to_classify = []
    for article in articles:
        title = article.get("title", "").strip()
        link = article.get("url") or article.get("link") or ""
        link = link.strip()
        source_name = article.get("source_name") or article.get("source") or ""
        description = article.get("description") or ""
        batch_to_classify.append({
            "title": title,
            "source_name": source_name,
            "description": description,
            "url": link,
            "link": link,
        })
    # print(batch_to_classify)

    try:
        accepted_rows = classify_news_articles(
            batch_to_classify,
            hash_fn=hash_url,
            status_writer=mark_url_status_in_memory,
        )
    except Exception as exc:
        print(f"Classification failed in local mode: {exc}")
        return {
            "statusCode": 503,
            "body": json.dumps({
                "error": "Gemini classification unavailable",
                "detail": str(exc),
                "message": "Local script executed, but article classification was skipped because the AI service is unavailable."
            })
        }

    accepted_lookup = {
        (article.get("title") or "").strip().lower(): article
        for article in batch_to_classify
        if (article.get("title") or "").strip()
    }
    winning_articles = [
        accepted_lookup[(row.get("title") or "").strip().lower()]
        for row in accepted_rows
        if row.get("should_send") and (row.get("title") or "").strip().lower() in accepted_lookup
    ]
    summary_by_title = {}

    if winning_articles:
        try:
            summaries = summarize_articles(winning_articles)
            summary_by_title = {
                (item.get("title") or "").strip().lower(): (item.get("summary") or "").strip()
                for item in summaries
                if (item.get("title") or "").strip()
            }
        except Exception as exc:
            print(f"Summary generation failed in local mode: {exc}")
            summary_by_title = {}

    print(accepted_rows)
    matches_found = 0
    for row in accepted_rows:
        if not row.get("should_send"):
            continue

        url = row.get("url")
        title = row.get("title")
        if not url:
            continue

        summary = summary_by_title.get((title or "").lower(), "")
        country = row.get("country")
        news_type = row.get("news_type")

        detail_lines = []
        if country:
            detail_lines.append(f"*Country:* {country}")
        if news_type:
            detail_lines.append(f"*Type:* {news_type}")

        details = "\n".join(detail_lines)
        if details:
            details = f"\n\n{details}"

        message = f"🚨 *New Finance Article Found!*\n\n*Title:* {title}{details}\n\n*Summary:* {summary}\n\n[Read Article here]({url})"
        result = send_telegram_message(message)
        if result is not None:
            mark_url_status_in_memory(url, "sent")
            matches_found += 1

    return {
        "statusCode": 200,
        "body": json.dumps(f"Scraper executed successfully. Sent {matches_found} alerts.")
    }


# --- LOCAL TESTING BOILERPLATE ---
if __name__ == "__main__":
    print("Testing Lambda function locally...")
    print(TELEGRAM_BOT_TOKEN)
    print(TELEGRAM_CHAT_ID)
    result = lambda_handler(event={}, context=None)
    print(f"Lambda Output: {result}")
    # send_telegram_message("hello")





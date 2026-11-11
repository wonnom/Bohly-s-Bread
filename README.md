# Bohly's Bread News Alert Bot

This project monitors finance and macro news from multiple sources, filters for relevant APAC investment and infrastructure stories, summarizes the most relevant items, and sends Telegram alerts when new material stories are discovered.

## What it does

- Pulls articles from NewsData and Finlight
- Deduplicates articles using a hash-based cache
- Uses Gemini to filter for meaningful APAC infrastructure, M&A, policy, and macro stories
- Adds country and news type metadata for accepted items
- Summarizes accepted stories in a second LLM pass
- Sends Telegram alerts for new matches
- Supports local runs using a JSON cache file and AWS Lambda/S3 when deployed

## Project structure

- `main.py` — main orchestration logic and local Lambda-like entry point
- `search_sources.py` — fetches data from NewsData and Finlight APIs
- `llm.py` — Gemini-based classification and summary logic
- `data/seen_urls.json` — local deduplication cache used when S3 is not configured
- `.env` — local environment variables
- `build/` — packaged duplicate copy, kept in sync with the main project files for deployment use

## Requirements

- Python 3.11+
- A valid Gemini API key
- A Telegram bot token and chat ID
- NewsData API key
- Finlight API key

## Setup

1. Create a virtual environment:

```bash
cd "/Users/weesi/Downloads/Personal Code/Bohly's Bread"
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install --upgrade pip
pip install boto3 python-dotenv google-genai finlight-client
```

3. Create a `.env` file in the project root with the following values:

```env
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.8-flash
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
NEWSDATA_API_KEY=your_newsdata_key
FINLIGHT_API_KEY=your_finlight_key
TOPICS="central bank digital currencies, infrastructure, energy, banking"
S3_BUCKET_NAME=your_optional_aws_bucket_name
```

Notes:
- `S3_BUCKET_NAME` is optional for local development.
- If it is not set, the app falls back to the local cache in `data/seen_urls.json`.

## Running locally

Start the script:

```bash
cd "/Users/weesi/Downloads/Personal Code/Bohly's Bread"
source .venv/bin/activate
python main.py
```

This runs the same Lambda-style entrypoint in local mode. If the AI service is temporarily unavailable or quota is exhausted, the script will return a controlled error instead of crashing.

## How the app works

1. It fetches articles from NewsData and Finlight based on the configured topics.
2. It deduplicates based on article URL hash.
3. It sends the candidate articles to Gemini for classification.
4. Only accepted items are summarized and sent to Telegram.
5. It keeps a local deduplication file at `data/seen_urls.json` unless AWS S3 is configured.

## AWS / Lambda notes

When deployed as a Lambda function, the app uses `S3_BUCKET_NAME` to read and write the deduplication state. If the environment variable is missing, it automatically falls back to the local JSON file for local development.

## Troubleshooting

### Local script crashes on S3

This is expected if `S3_BUCKET_NAME` is not set in a local environment. The app now checks for this condition and falls back to the local file cache.

### Gemini quota error

If Gemini returns a `429 RESOURCE_EXHAUSTED` error, the current API quota has been exceeded. The script handles this gracefully in local mode, but live filtering will pause until quota resets.

### Telegram message not sending

Check:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- whether the bot has permission to send to that chat

## License

This project is for personal use and experimentation unless otherwise specified.

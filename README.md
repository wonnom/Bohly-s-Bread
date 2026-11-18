# Bohly's Bread News Brief

Collects Asia-Pacific project finance, banking and energy news, filters it, and sends a morning and evening brief to Telegram.

## How it works

| Step | When | What happens |
|---|---|---|
| Collect | Every 2 hours | Fetches RSS feeds, NewsData and Finlight, applies code filters, removes duplicates and queues articles. No LLM calls |
| Brief | 07:30 and 19:30 SGT | Sends up to 40 queued articles to Gemini, summarises the best 12 or fewer and sends them to Telegram |

If Gemini is unavailable, queued articles are kept and the next collect run retries the brief.

## Project structure

- `main.py`: Lambda entry point and pipeline
- `src/feeds.py`: search queries and RSS feeds
- `src/search_sources.py`: fetches and standardises articles
- `src/ranking.py`: code filters, ordering and same-event dedupe
- `src/llm.py`: Gemini filter and summary
- `src/telegram.py`: message formatting and sending
- `src/storage.py`: history and pending list, in S3 on AWS or `data/` locally
- `src/report.py`: 7-day trial spreadsheet
- `src/sheet_sync.py`: adds trial articles to the Google rating sheet
- `apps_script/Code.gs`: script that runs inside the Google Sheet
- `tests/`: automated tests
- `docs/DEPLOYMENT.md`: CI/CD and AWS setup

## Setup

Requires Python 3.13.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Create a `.env` file:

```env
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.8-flash
GEMINI_BACKUP_MODEL=gemini-3.5-flash
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
NEWSDATA_API_KEY=your_newsdata_key
FINLIGHT_API_KEY=your_finlight_key
S3_BUCKET_NAME=your_bucket_name
```

## Running locally

```bash
pytest                          # run the tests
python main.py collect --dry-run # fetch and filter without saving
python main.py collect           # fetch and queue articles
python main.py brief --dry-run   # print the brief without sending or saving
python main.py brief             # send the brief to Telegram
```

## Trial rating sheet

During the trial, articles go to a Google Sheet for rating and Telegram only receives an update alert. See [docs/RATING_SHEET.md](docs/RATING_SHEET.md) for setup.

## 7-day trial report

After the trial, sign in to AWS on your computer and create the rating spreadsheet from the S3 data:

```bash
python main.py report --days 7 --from-s3
```

The file is saved in `reports/`. Your AWS login needs read access to the bucket, and `S3_BUCKET_NAME` must be set in `.env`. Rate each article on the `Rate articles` sheet, and the `Summary`, `Feeds` and `Terms` sheets update as you go. An existing report is never overwritten.

## Deployment

GitHub runs the tests on every pull request. After merging to `main`, deploy with `scripts/deploy.sh`. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the setup.

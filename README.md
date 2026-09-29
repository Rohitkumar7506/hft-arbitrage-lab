# HFT Arbitrage Lab

An academic internship-inspired simulation for searching global market instruments,
reading the latest available quote, and exploring historical price data from the
Twelve Data API. This project does not execute trades and does not provide
financial advice.

## Features

- Search actual Twelve Data instruments by symbol or company name
- Exchange and country are shown exactly as returned by the API for each instrument
- Quick search: Apple (AAPL), Microsoft (MSFT), NVIDIA (NVDA), Amazon (AMZN), Tesla (TSLA)
- Search checks the Twelve Data API **and** the local cache (symbol, company name, exchange); API and cached results are merged with duplicates removed, and cached-only results are tagged `CACHED DATA`
- Automatic local JSON cache with fallback: `LIVE API DATA`, `CACHED DATA`, or `DATA UNAVAILABLE`
- View the selected instrument's latest available quote
- View 1D, 1W, 1M, 3M, or 1Y historical price data in Chart.js
- Manual refresh only; there is no automatic market polling
- Clear API, rate-limit, missing-data, and unavailable-data states
- API key stays on the Flask server and is never sent to browser JavaScript

## Technologies

- Python
- Flask
- Requests
- python-dotenv
- HTML5, CSS3, Vanilla JavaScript
- Chart.js from CDN
- Twelve Data API

## Folder structure

```text
hft-arbitrage-lab/
├── app.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── cache/                  (created automatically, git-ignored)
│   └── market_cache.json
├── templates/
│   └── index.html
└── static/
    ├── css/
    │   └── style.css
    └── js/
        └── app.js
```

## Setup

The project expects a Twelve Data API key in the server environment:

```text
TWELVE_DATA_API_KEY=your_key_here
```

For a local Python run, copy `.env.example` to `.env` and replace the blank value.
In Replit, add `TWELVE_DATA_API_KEY` as a project secret instead. Never commit
`.env` or place the key in HTML, CSS, or JavaScript.

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run the Flask server:

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Cache and fallback

Every successful `/api/quote` and `/api/history` response is saved to
`cache/market_cache.json`, keyed by symbol + exchange (+ range for history). Only
instruments you actually request successfully are cached. If a later request fails
(rate limit, network error, API error), the latest cached copy is returned and the
UI shows `CACHED DATA` with the time it was saved. Fresh API responses show
`LIVE API DATA`. If the API fails and nothing is cached, the UI shows
`DATA UNAVAILABLE`. No prices are ever generated, estimated, or randomised, and
there is no automatic polling; refresh is manual.

### Searching the cache

`GET /api/search` always looks in `cache/market_cache.json` as well as calling
Twelve Data. If the API fails (no key, network error, rate limit), instruments that
were previously loaded successfully are still listed, can be opened, and load their
saved quote and history with `CACHED DATA`. Only ranges you already loaded are
cached; any other range shows `DATA UNAVAILABLE` rather than invented data.

## API routes

- `GET /` — dashboard
- `GET /api/status` — checks configuration and Twelve Data reachability
- `GET /api/search?q=AAPL` — searches Twelve Data instruments
- `GET /api/quote?symbol=AAPL&exchange=NASDAQ` — latest quote
- `GET /api/history?symbol=AAPL&exchange=NASDAQ&range=1M` — historical series

## Twelve Data limitations

Availability, intraday depth, request limits, and exchange coverage depend on the
Twelve Data plan and market session. If a range is unavailable, the dashboard
shows an unavailable state instead of fabricating data. The 1D view requests
5-minute data, while longer views use hourly or daily intervals. Manual refresh
is intentional to avoid unnecessary quota usage.
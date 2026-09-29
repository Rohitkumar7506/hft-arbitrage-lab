import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

app = Flask(__name__)

TWELVE_DATA_URL = "https://api.twelvedata.com"
REQUEST_TIMEOUT = 12
SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,60}$")

RANGE_CONFIG = {
    "1D": {"interval": "5min", "outputsize": 78},
    "1W": {"interval": "1hour", "outputsize": 60},
    "1M": {"interval": "1day", "outputsize": 31},
    "3M": {"interval": "1day", "outputsize": 90},
    "1Y": {"interval": "1day", "outputsize": 260},
}

# --- Data-source labels shown in the UI -------------------------------------
LIVE_LABEL = "LIVE API DATA"
CACHED_LABEL = "CACHED DATA"
UNAVAILABLE_LABEL = "DATA UNAVAILABLE"

# --- Local JSON cache --------------------------------------------------------
# Only responses that were actually received from Twelve Data are stored here.
# Nothing in this file is ever generated or estimated.
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
CACHE_FILE = CACHE_DIR / "market_cache.json"
_cache_lock = threading.Lock()
CACHE_SEARCH_LIMIT = 8  # max cache-only extras added to a search


class MarketDataError(Exception):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def twelve_data_key() -> str:
    return os.getenv("TWELVE_DATA_API_KEY", "").strip()


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def cache_key(*parts: str) -> str:
    return "|".join((part or "").strip().upper() for part in parts)


def _read_cache_file() -> dict[str, dict[str, Any]]:
    empty: dict[str, dict[str, Any]] = {"quotes": {}, "history": {}}
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    for section in ("quotes", "history"):
        if not isinstance(data.get(section), dict):
            data[section] = {}
    return data


def read_cache(section: str, key: str) -> dict[str, Any] | None:
    with _cache_lock:
        entry = _read_cache_file().get(section, {}).get(key)
    if isinstance(entry, dict) and isinstance(entry.get("data"), dict):
        return entry
    return None


def write_cache(section: str, key: str, data: dict[str, Any]) -> None:
    """Save a successful API result. Failures here must never break a live response."""
    try:
        with _cache_lock:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache = _read_cache_file()
            cache[section][key] = {
                "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "data": data,
            }
            temp_file = CACHE_FILE.with_suffix(".json.tmp")
            with temp_file.open("w", encoding="utf-8") as handle:
                json.dump(cache, handle, ensure_ascii=False, indent=2)
            os.replace(temp_file, CACHE_FILE)
    except (OSError, TypeError, ValueError):
        app.logger.exception("Could not write market-data cache")


def find_cached(
    section: str, symbol: str, exchange: str, selected_range: str | None = None
) -> dict[str, Any] | None:
    """Look up a cached quote/history entry.

    Tries the exact key first. If that misses (for example the entry was saved
    without an exchange, or with a differently cased symbol), falls back to
    matching the symbol/exchange/range stored inside the cached data itself.
    """
    parts = [symbol, exchange] + ([selected_range] if selected_range else [])
    exact = read_cache(section, cache_key(*parts))
    if exact:
        return exact

    with _cache_lock:
        entries = _read_cache_file().get(section, {}).values()
    wanted_symbol = symbol.strip().upper()
    wanted_exchange = exchange.strip().upper()
    matches = []
    for entry in entries:
        data = entry.get("data") if isinstance(entry, dict) else None
        if not isinstance(data, dict):
            continue
        if str(data.get("symbol", "")).strip().upper() != wanted_symbol:
            continue
        if selected_range and str(data.get("range", "")).strip().upper() != selected_range:
            continue
        stored_exchange = str(data.get("exchange", "")).strip().upper()
        if wanted_exchange and stored_exchange not in (wanted_exchange, "", "N/A"):
            continue
        matches.append((stored_exchange, entry))

    # With no exchange requested, only accept the match if it is unambiguous.
    if not wanted_exchange and len({exchange_ for exchange_, _ in matches}) > 1:
        return None
    if not matches:
        return None
    return max(matches, key=lambda item: str(item[1].get("saved_at", "")))[1]


# ---------------------------------------------------------------------------
# Searching the local cache
# ---------------------------------------------------------------------------
def _known(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    return text if text and text != "N/A" else ""


def cached_instruments() -> list[dict[str, str]]:
    """List every instrument that has real, previously saved API data.

    Built only from what is already in the cache: quotes first (they carry the
    company name), then any instrument that only has saved history.
    """
    with _cache_lock:
        cache = _read_cache_file()

    found: dict[tuple[str, str], dict[str, str]] = {}

    def remember(data: dict[str, Any], key: str, has_name: bool) -> None:
        key_symbol, _, key_exchange = key.partition("|")
        symbol = _known(data.get("symbol")) or _known(key_symbol)
        if not symbol:
            return
        exchange = _known(data.get("exchange")) or _known(key_exchange)
        identity = (symbol.upper(), exchange.upper())
        if identity in found:
            return
        name = _known(data.get("company")) if has_name else ""
        found[identity] = {
            "symbol": symbol,
            "name": name or symbol,
            "exchange": exchange or "N/A",
            "country": _known(data.get("country")) or "N/A",
            "type": "N/A",  # quotes do not carry an instrument type
            "currency": _known(data.get("currency")) or "N/A",
            "source": "cache",
        }

    for key, entry in cache["quotes"].items():
        if isinstance(entry, dict) and isinstance(entry.get("data"), dict):
            remember(entry["data"], key, has_name=True)
    for key, entry in cache["history"].items():
        if isinstance(entry, dict) and isinstance(entry.get("data"), dict):
            remember(entry["data"], key, has_name=False)
    return list(found.values())


def cache_match_rank(item: dict[str, str], query: str) -> int | None:
    """Lower is better; None means no match. Searches symbol, name, exchange."""
    needle = query.strip().casefold()
    if not needle:
        return None
    symbol = item["symbol"].casefold()
    name = item["name"].casefold()
    exchange = item["exchange"].casefold()
    haystack = f"{symbol} {name} {exchange}"
    if not all(token in haystack for token in needle.split()):
        return None
    if symbol == needle:
        return 0
    if symbol.startswith(needle):
        return 1
    if name.startswith(needle):
        return 2
    if needle in symbol or needle in name:
        return 3
    return 4


def search_cache(query: str) -> list[dict[str, str]]:
    ranked = []
    for item in cached_instruments():
        rank = cache_match_rank(item, query)
        if rank is not None:
            ranked.append((rank, item["name"].casefold(), item))
    ranked.sort(key=lambda entry: (entry[0], entry[1]))
    return [item for _, _, item in ranked]


def merge_search_results(
    api_results: list[dict[str, str]], cached_results: list[dict[str, str]]
) -> list[dict[str, str]]:
    """API results first (unchanged order), then cached instruments not already listed."""
    merged = [{**item, "source": "api"} for item in api_results]
    seen = {(item["symbol"].upper(), item["exchange"].upper()) for item in merged}
    api_symbols = {symbol for symbol, _ in seen}
    for item in cached_results:
        identity = (item["symbol"].upper(), item["exchange"].upper())
        if identity in seen:
            continue
        # A cached entry with no known exchange duplicates any API listing of the symbol.
        if item["exchange"] == "N/A" and identity[0] in api_symbols:
            continue
        seen.add(identity)
        merged.append(item)
    return merged


# ---------------------------------------------------------------------------
# Twelve Data helpers
# ---------------------------------------------------------------------------
def user_error_for_response(response: requests.Response, payload: Any) -> MarketDataError:
    code = payload.get("code") if isinstance(payload, dict) else None
    message = payload.get("message") if isinstance(payload, dict) else None
    status = response.status_code
    combined = f"{code} {message or ''}".lower()

    if status == 429 or "rate limit" in combined or "too many" in combined or "run out of api credits" in combined:
        return MarketDataError(
            "Market data request limit reached. Please try again later.", 429
        )
    if status in (401, 403) or "api key" in combined or "apikey" in combined:
        return MarketDataError(
            "Market data is currently unavailable. Please check the API connection.",
            503,
        )
    if "symbol" in combined and ("not found" in combined or "invalid" in combined):
        return MarketDataError("No matching stock was found.", 404)
    return MarketDataError(
        "Market data is currently unavailable. Please check the API connection.", 503
    )


def call_twelve_data(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    api_key = twelve_data_key()
    if not api_key:
        raise MarketDataError(
            "Market data is currently unavailable. Please check the API connection.",
            503,
        )

    try:
        response = requests.get(
            f"{TWELVE_DATA_URL}/{endpoint}",
            params={**params, "apikey": api_key},
            timeout=REQUEST_TIMEOUT,
        )
        payload = response.json()
    except (requests.RequestException, ValueError):
        app.logger.exception("Twelve Data request failed for %s", endpoint)
        raise MarketDataError(
            "Market data is currently unavailable. Please check the API connection.",
            503,
        )

    if not response.ok or (
        isinstance(payload, dict) and payload.get("status") == "error"
    ):
        raise user_error_for_response(response, payload)

    return payload


def clean_symbol(value: str | None) -> str:
    symbol = (value or "").strip()
    if not symbol or not SYMBOL_PATTERN.fullmatch(symbol):
        raise MarketDataError("Please enter a valid stock symbol.", 400)
    return symbol


def clean_exchange(value: str | None) -> str:
    exchange = (value or "").strip()
    if len(exchange) > 30 or (exchange and not re.fullmatch(r"[A-Za-z0-9 ._-]+", exchange)):
        raise MarketDataError("Please select a valid exchange.", 400)
    return exchange


def result_value(result: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = result.get(key)
        if value not in (None, ""):
            return str(value)
    return "N/A"


def normalized_search_result(result: dict[str, Any]) -> dict[str, str]:
    return {
        "symbol": result_value(result, "symbol"),
        "name": result_value(result, "instrument_name", "name"),
        "exchange": result_value(result, "exchange", "mic_code"),
        "country": result_value(result, "country"),
        "type": result_value(result, "type", "instrument_type"),
        "currency": result_value(result, "currency"),
    }


def display_number(value: Any) -> str:
    if value in (None, "", "N/A"):
        return "N/A"
    try:
        number = float(value)
        return f"{number:,.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def normalized_quote(payload: dict[str, Any], symbol: str, exchange: str) -> dict[str, Any]:
    return {
        "company": result_value(payload, "name", "instrument_name"),
        "symbol": result_value(payload, "symbol") if payload.get("symbol") else symbol,
        "exchange": result_value(payload, "exchange") if payload.get("exchange") else exchange or "N/A",
        "country": result_value(payload, "country"),
        "currency": result_value(payload, "currency"),
        "price": display_number(payload.get("close") or payload.get("price")),
        "change": display_number(payload.get("change")),
        "change_percent": display_number(payload.get("percent_change")),
        "open": display_number(payload.get("open")),
        "high": display_number(payload.get("high")),
        "low": display_number(payload.get("low")),
        "previous_close": display_number(payload.get("previous_close")),
        "volume": display_number(payload.get("volume")),
        "datetime": result_value(payload, "datetime"),
        "market_status": (
            "Open"
            if payload.get("is_market_open") is True
            else "Closed"
            if payload.get("is_market_open") is False
            else "Unknown"
        ),
    }


def unavailable_response(error: MarketDataError):
    return (
        jsonify(
            {
                "message": error.message,
                "data_status": "unavailable",
                "data_label": UNAVAILABLE_LABEL,
            }
        ),
        error.status_code,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
def dashboard():
    return render_template("index.html")


@app.get("/api/status")
def api_status():
    if not twelve_data_key():
        return jsonify(
            {
                "configured": False,
                "reachable": False,
                "label": "API DISCONNECTED",
                "message": "Add a Twelve Data API key to connect market data.",
            }
        )

    try:
        call_twelve_data("symbol_search", {"symbol": "AAPL", "outputsize": 1})
        return jsonify(
            {
                "configured": True,
                "reachable": True,
                "label": "API CONNECTED",
                "message": "Twelve Data is reachable.",
            }
        )
    except MarketDataError as error:
        return jsonify(
            {
                "configured": True,
                "reachable": False,
                "label": "API DISCONNECTED",
                "message": error.message,
            }
        )


@app.get("/api/search")
def search():
    query = request.args.get("q", "").strip()
    if len(query) < 1:
        return jsonify({"results": [], "message": "Enter a stock symbol or company name."}), 400
    if len(query) > 80:
        return jsonify({"results": [], "message": "Search text is too long."}), 400

    api_results: list[dict[str, str]] = []
    api_error: MarketDataError | None = None
    try:
        payload = call_twelve_data("symbol_search", {"symbol": query})
        raw_results = payload.get("data", [])
        api_results = [
            normalized_search_result(item)
            for item in raw_results
            if isinstance(item, dict) and result_value(item, "symbol") != "N/A"
        ]
        # Exact symbol matches first; otherwise keep the API's own ordering
        # (sort is stable). No country or exchange is favoured.
        wanted = query.upper()
        api_results.sort(key=lambda item: 0 if item["symbol"].upper() == wanted else 1)
        api_results = api_results[:12]
    except MarketDataError as error:
        api_error = error

    # Always look in the local cache too, so an instrument that was already
    # loaded successfully can be found even when the API is down.
    cached_results = search_cache(query)[:CACHE_SEARCH_LIMIT]
    results = merge_search_results(api_results, cached_results)

    if api_error and not results:
        return jsonify({"results": [], "message": api_error.message}), api_error.status_code

    if api_error:
        return jsonify(
            {
                "results": results,
                "data_status": "cached",
                "data_label": CACHED_LABEL,
                "message": "Live search is unavailable. Showing instruments saved in the local cache.",
            }
        )
    return jsonify(
        {"results": results, "data_status": "live", "data_label": LIVE_LABEL}
    )


@app.get("/api/quote")
def quote():
    try:
        symbol = clean_symbol(request.args.get("symbol"))
        exchange = clean_exchange(request.args.get("exchange"))
    except MarketDataError as error:
        return jsonify({"message": error.message}), error.status_code

    key = cache_key(symbol, exchange)
    try:
        params: dict[str, Any] = {"symbol": symbol}
        if exchange and exchange != "N/A":
            params["exchange"] = exchange
        payload = call_twelve_data("quote", params)
        data = normalized_quote(payload, symbol, exchange)
        if data["price"] == "N/A":
            raise MarketDataError("No quote data is available for this stock.", 404)
    except MarketDataError as error:
        cached = find_cached("quotes", symbol, exchange)
        if cached:
            data = dict(cached["data"])
            data["market_status"] = "Unknown"  # a stored status may be stale
            return jsonify(
                {
                    "quote": data,
                    "data_status": "cached",
                    "data_label": CACHED_LABEL,
                    "cached_at": cached.get("saved_at"),
                }
            )
        return unavailable_response(error)

    write_cache("quotes", key, data)
    return jsonify(
        {
            "quote": data,
            "data_status": "live",
            "data_label": LIVE_LABEL,
            "cached_at": None,
        }
    )


@app.get("/api/history")
def history():
    try:
        symbol = clean_symbol(request.args.get("symbol"))
        exchange = clean_exchange(request.args.get("exchange"))
    except MarketDataError as error:
        return jsonify({"message": error.message}), error.status_code

    selected_range = request.args.get("range", "1M").upper()
    config = RANGE_CONFIG.get(selected_range)
    if not config:
        return jsonify({"message": "Please select a valid historical range."}), 400

    key = cache_key(symbol, exchange, selected_range)
    try:
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": config["interval"],
            "outputsize": config["outputsize"],
            # Timestamps are returned in each instrument's own exchange timezone.
            "timezone": "Exchange",
        }
        if exchange and exchange != "N/A":
            params["exchange"] = exchange
        payload = call_twelve_data("time_series", params)
        values = payload.get("values", [])
        points = []
        for value in reversed(values if isinstance(values, list) else []):
            if not isinstance(value, dict) or value.get("close") in (None, ""):
                continue
            try:
                points.append(
                    {
                        "time": value.get("datetime", "N/A"),
                        "price": float(value["close"]),
                    }
                )
            except (TypeError, ValueError):
                continue
        if not points:
            raise MarketDataError("No historical data available.", 404)
    except MarketDataError as error:
        cached = find_cached("history", symbol, exchange, selected_range)
        if cached:
            return jsonify(
                {
                    **cached["data"],
                    "data_status": "cached",
                    "data_label": CACHED_LABEL,
                    "cached_at": cached.get("saved_at"),
                }
            )
        return unavailable_response(error)

    data = {
        "symbol": symbol,
        "exchange": exchange or "N/A",
        "range": selected_range,
        "interval": config["interval"],
        "points": points,
    }
    write_cache("history", key, data)
    return jsonify(
        {**data, "data_status": "live", "data_label": LIVE_LABEL, "cached_at": None}
    )


@app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"):
        return jsonify({"message": "The requested market-data route was not found."}), 404
    return render_template("index.html"), 200


@app.errorhandler(500)
def internal_error(error):
    app.logger.exception("Unexpected server error")
    if request.path.startswith("/api/"):
        return jsonify(
            {"message": "Market data is currently unavailable. Please try again later."}
        ), 500
    return render_template("index.html"), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)

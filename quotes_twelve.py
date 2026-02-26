from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib import error, parse, request
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")
API_BASE_URL = "https://api.twelvedata.com"


class TwelveDataQuoteError(RuntimeError):
    """Raised when Twelve Data quote retrieval fails."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class QuoteSnapshot:
    ticker: str
    last_price: float | None
    prev_close: float | None
    prev_high: float | None
    prev_low: float | None
    prev_day: str | None
    quote_available: bool

    @classmethod
    def unavailable(cls, ticker: str) -> "QuoteSnapshot":
        return cls(
            ticker=ticker,
            last_price=None,
            prev_close=None,
            prev_high=None,
            prev_low=None,
            prev_day=None,
            quote_available=False,
        )

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "QuoteSnapshot":
        return cls(
            ticker=str(payload.get("ticker", "")).upper(),
            last_price=_to_float(payload.get("last_price")),
            prev_close=_to_float(payload.get("prev_close")),
            prev_high=_to_float(payload.get("prev_high")),
            prev_low=_to_float(payload.get("prev_low")),
            prev_day=payload.get("prev_day") if payload.get("prev_day") else None,
            quote_available=bool(payload.get("quote_available", False)),
        )


class TwelveDataQuotesClient:
    """Fetches last price + previous completed day OHLC from Twelve Data."""

    def __init__(
        self,
        cache_path: str | Path = ".cache/twelve_data_quotes.json",
        cache_ttl_seconds: int = 300,
        max_retries: int = 3,
        lookback_days: int = 60,
        max_credits_per_minute: int = 8,
    ) -> None:
        try:
            api_key = os.environ["TWELVE_DATA_API_KEY"]
        except KeyError as exc:
            raise TwelveDataQuoteError(
                "Missing required environment variable: TWELVE_DATA_API_KEY",
                retryable=False,
            ) from exc

        self.api_key = api_key
        self.cache_path = Path(cache_path)
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_retries = max_retries
        self.lookback_days = lookback_days
        self.max_credits_per_minute = max_credits_per_minute

        self._window_start_monotonic: float | None = None
        self._window_used_credits = 0

    def get_quotes(self, tickers: Sequence[str]) -> dict[str, QuoteSnapshot]:
        if not tickers:
            return {}

        cache = _load_cache(self.cache_path)
        now_utc = datetime.now(timezone.utc)

        result: dict[str, QuoteSnapshot] = {}
        provider_error: str | None = None
        skipped_due_provider_error = 0

        for raw_ticker in tickers:
            ticker = raw_ticker.upper().strip()
            if not ticker:
                continue

            if provider_error is not None:
                skipped_due_provider_error += 1
                result[ticker] = QuoteSnapshot.unavailable(ticker)
                continue

            cached = _read_cache_entry(
                cache=cache,
                ticker=ticker,
                now_utc=now_utc,
                ttl_seconds=self.cache_ttl_seconds,
            )
            if cached is not None:
                result[ticker] = cached
                continue

            try:
                snapshot = self._fetch_quote_snapshot(ticker)
            except TwelveDataQuoteError as exc:
                logger.warning("Twelve Data quote fetch failed for %s: %s", ticker, exc)
                snapshot = QuoteSnapshot.unavailable(ticker)
                if _is_provider_level_error(str(exc)):
                    provider_error = str(exc)
            result[ticker] = snapshot

            if snapshot.quote_available:
                _write_cache_entry(cache=cache, ticker=ticker, snapshot=snapshot, now_utc=now_utc)

        _save_cache(self.cache_path, cache)

        if provider_error is not None and skipped_due_provider_error > 0:
            logger.warning(
                "Skipped quote fetch for %s tickers due to provider-level Twelve Data error: %s",
                skipped_due_provider_error,
                provider_error,
            )

        return result

    def _fetch_quote_snapshot(self, ticker: str) -> QuoteSnapshot:
        payload = self._with_retries(
            fn=lambda: self._request_daily_series(ticker=ticker),
            context=f"time_series({ticker})",
        )

        values = payload.get("values")
        if not isinstance(values, list) or not values:
            return QuoteSnapshot.unavailable(ticker)

        bars = _extract_sorted_bars(values)
        if not bars:
            return QuoteSnapshot.unavailable(ticker)

        newest_date, newest_bar = bars[0]
        last_price = _round_price(_to_float(newest_bar.get("close")))

        today_ny = datetime.now(NY_TZ).date()
        prev_day_date, prev_bar = _pick_prev_completed_bar(bars, today_ny)
        if prev_day_date is None or prev_bar is None:
            return QuoteSnapshot.unavailable(ticker)

        prev_close = _round_price(_to_float(prev_bar.get("close")))
        prev_high = _round_price(_to_float(prev_bar.get("high")))
        prev_low = _round_price(_to_float(prev_bar.get("low")))

        quote_available = all(value is not None for value in (last_price, prev_close, prev_high, prev_low))
        if not quote_available:
            return QuoteSnapshot.unavailable(ticker)

        return QuoteSnapshot(
            ticker=ticker,
            last_price=last_price,
            prev_close=prev_close,
            prev_high=prev_high,
            prev_low=prev_low,
            prev_day=prev_day_date.isoformat(),
            quote_available=True,
        )

    def _request_daily_series(self, ticker: str) -> dict[str, Any]:
        outputsize = min(max(self.lookback_days + 2, 6), 120)
        return self._get_json(
            endpoint="/time_series",
            params={
                "symbol": ticker,
                "interval": "1day",
                "outputsize": str(outputsize),
                "timezone": "America/New_York",
                "apikey": self.api_key,
            },
        )

    def _get_json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        self._wait_for_credit()

        query = parse.urlencode(params)
        url = f"{API_BASE_URL}{endpoint}?{query}"
        req = request.Request(
            url=url,
            headers={
                "User-Agent": "MarketScout/2.0",
                "Accept": "application/json",
            },
        )

        try:
            with request.urlopen(req, timeout=20) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                raw = response.read().decode(charset, errors="replace")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429 or exc.code >= 500:
                raise TwelveDataQuoteError(f"HTTP {exc.code}: {body[:280]}", retryable=True) from exc
            raise TwelveDataQuoteError(f"HTTP {exc.code}: {body[:280]}", retryable=False) from exc
        except (error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise TwelveDataQuoteError(f"Network error: {exc}", retryable=True) from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TwelveDataQuoteError("Invalid JSON in Twelve Data response", retryable=True) from exc

        if not isinstance(payload, dict):
            raise TwelveDataQuoteError("Unexpected Twelve Data response schema", retryable=False)

        api_error = _extract_api_error(payload)
        if api_error is not None:
            code, message = api_error
            raise TwelveDataQuoteError(
                f"{code}: {message}",
                retryable=_is_retryable_api_error(code=code, message=message),
            )

        return payload

    def _with_retries(self, fn, context: str):
        for attempt in range(1, self.max_retries + 1):
            try:
                return fn()
            except TwelveDataQuoteError as exc:
                if not exc.retryable:
                    raise TwelveDataQuoteError(f"{context} non-retryable failure: {exc}", retryable=False) from exc

                if attempt >= self.max_retries:
                    raise TwelveDataQuoteError(f"{context} failed after retries: {exc}", retryable=False) from exc

                sleep_for = self._retry_wait_seconds(attempt, str(exc))
                logger.warning(
                    "Twelve Data transient error in %s (attempt %s/%s): %s. Retrying in %.1fs.",
                    context,
                    attempt,
                    self.max_retries,
                    exc,
                    sleep_for,
                )
                time.sleep(sleep_for)

    def _wait_for_credit(self) -> None:
        if self.max_credits_per_minute <= 0:
            return

        now = time.monotonic()
        if self._window_start_monotonic is None:
            self._window_start_monotonic = now
            self._window_used_credits = 0

        elapsed = now - self._window_start_monotonic
        if elapsed >= 60.0:
            self._window_start_monotonic = now
            self._window_used_credits = 0

        if self._window_used_credits >= self.max_credits_per_minute:
            wait_for = max(0.2, 60.0 - (now - self._window_start_monotonic) + 0.2)
            logger.info(
                "Twelve Data rate limiter: waiting %.1fs to respect %s credits/min.",
                wait_for,
                self.max_credits_per_minute,
            )
            time.sleep(wait_for)
            self._window_start_monotonic = time.monotonic()
            self._window_used_credits = 0

        self._window_used_credits += 1

    def _retry_wait_seconds(self, attempt: int, error_message: str) -> float:
        msg = error_message.lower()
        if "current minute" in msg or "limit being" in msg or "http 429" in msg:
            return max(1.0, self._seconds_until_window_reset() + 0.25)

        base = min(8.0, 2 ** (attempt - 1))
        return base + random.uniform(0.0, 0.6)

    def _seconds_until_window_reset(self) -> float:
        if self._window_start_monotonic is None:
            return 1.0

        elapsed = time.monotonic() - self._window_start_monotonic
        if elapsed >= 60.0:
            return 1.0

        return max(1.0, 60.0 - elapsed)


def _extract_api_error(payload: dict[str, Any]) -> tuple[str, str] | None:
    status = str(payload.get("status", "")).lower()
    code = str(payload.get("code", "error")).strip() or "error"
    message = str(payload.get("message", payload.get("error", ""))).strip()

    if status == "error":
        return code, message or "API returned status=error"

    has_error_shape = message and ("values" not in payload and "close" not in payload)
    if has_error_shape:
        return code, message

    return None


def _is_retryable_api_error(code: str, message: str) -> bool:
    code_str = str(code).lower()
    msg = message.lower()

    if code_str in {"429", "500", "502", "503", "504"}:
        return True

    non_retryable_tokens = [
        "api key",
        "unauthorized",
        "not authorized",
        "not entitled",
        "invalid symbol",
        "unknown symbol",
        "forbidden",
        "error code: 1010",
    ]
    if any(token in msg for token in non_retryable_tokens):
        return False

    if code_str in {"401", "403", "404", "414", "422"}:
        return False

    return True


def _is_provider_level_error(message: str) -> bool:
    msg = message.lower()
    provider_tokens = [
        "api key",
        "unauthorized",
        "not authorized",
        "not entitled",
        "forbidden",
        "error code: 1010",
        "upgrade your plan",
    ]
    return any(token in msg for token in provider_tokens)


def _extract_sorted_bars(values: Sequence[Any]) -> list[tuple[date, dict[str, Any]]]:
    bars: list[tuple[date, dict[str, Any]]] = []

    for item in values:
        if not isinstance(item, dict):
            continue
        bar_date = _extract_bar_date(item)
        if bar_date is None:
            continue
        bars.append((bar_date, item))

    bars.sort(key=lambda entry: entry[0], reverse=True)
    return bars


def _pick_prev_completed_bar(
    bars: Sequence[tuple[date, dict[str, Any]]],
    today_ny: date,
) -> tuple[date | None, dict[str, Any] | None]:
    for bar_date, bar in bars:
        if bar_date < today_ny:
            return bar_date, bar

    if len(bars) >= 2:
        return bars[1]

    if len(bars) == 1 and bars[0][0] <= today_ny:
        return bars[0]

    return None, None


def _extract_bar_date(bar: dict[str, Any]) -> date | None:
    raw = str(bar.get("datetime", "")).strip()
    if not raw:
        return None

    date_part = raw.split(" ", 1)[0]
    if "T" in date_part:
        date_part = date_part.split("T", 1)[0]

    try:
        return date.fromisoformat(date_part)
    except ValueError:
        return None


def _read_cache_entry(
    cache: dict[str, Any],
    ticker: str,
    now_utc: datetime,
    ttl_seconds: int,
) -> QuoteSnapshot | None:
    payload = cache.get("quotes", {}).get(ticker)
    if not isinstance(payload, dict):
        return None

    fetched_at_raw = payload.get("fetched_at")
    if not isinstance(fetched_at_raw, str):
        return None

    try:
        fetched_dt = datetime.fromisoformat(fetched_at_raw)
    except ValueError:
        return None

    if fetched_dt.tzinfo is None:
        fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)

    age_seconds = (now_utc - fetched_dt.astimezone(timezone.utc)).total_seconds()
    if age_seconds > ttl_seconds:
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    snapshot = QuoteSnapshot.from_json(data)
    if not snapshot.quote_available:
        return None
    return snapshot


def _write_cache_entry(cache: dict[str, Any], ticker: str, snapshot: QuoteSnapshot, now_utc: datetime) -> None:
    cache.setdefault("quotes", {})[ticker] = {
        "fetched_at": now_utc.isoformat(),
        "data": asdict(snapshot),
    }


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"quotes": {}}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read Twelve Data cache file %s: %s", path, exc)

    return {"quotes": {}}


def _save_cache(path: Path, cache: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write Twelve Data cache file %s: %s", path, exc)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_price(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)

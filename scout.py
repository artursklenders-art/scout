from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

from config import ConfigError, load_config
from email_sender import EmailSendError, send_plain_email
from market_data import Headline, collect_market_news, extract_ticker_mentions
from openai_client import (
    NarrativeSections,
    OpenAIClientError,
    fallback_narrative_sections,
    generate_narrative_sections,
)
from quotes_twelve import QuoteSnapshot, TwelveDataQuotesClient
from zones import ZonePlan, build_long_plan, build_short_plan, format_range, format_target, make_price_levels


TRADE_UNIVERSE = [
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "TSLA",
    "GOOGL",
    "AMD",
    "NFLX",
]

PULSE_TICKERS = ["SPY", "QQQ", "IWM", "TLT", "VXX"]
WATCHLIST_SIZE = 12

MACRO_SOURCES = [
    "https://www.investing.com/economic-calendar/",
    "https://www.forexfactory.com/calendar",
    "https://www.federalreserve.gov/newsevents/calendar.htm",
]


@dataclass(frozen=True)
class TradeIdea:
    ticker: str
    side: str
    setup_label: str
    quote: QuoteSnapshot
    why_today: str
    zone: ZonePlan | None


def main() -> int:
    _setup_logging()
    args = _parse_args()

    try:
        config = load_config()
    except ConfigError as exc:
        logging.error("Configuration error: %s", exc)
        return 1

    now_local = datetime.now(ZoneInfo(config.timezone))

    logging.info("Collecting market news package...")
    headlines = collect_market_news(
        max_items=config.news_max_items,
        lookback_hours=config.news_lookback_hours,
        timeout_seconds=config.rss_timeout_seconds,
        user_agent=config.user_agent,
    )

    ticker_mentions = dict(extract_ticker_mentions(headlines=headlines, max_items=30))

    all_tickers = _dedupe_tickers([*TRADE_UNIVERSE, *PULSE_TICKERS])
    quotes_client = TwelveDataQuotesClient(
        cache_ttl_seconds=config.twelve_data_cache_ttl_seconds,
        max_retries=config.twelve_data_max_retries,
        lookback_days=config.twelve_data_lookback_days,
        max_credits_per_minute=config.twelve_data_max_credits_per_minute,
    )

    logging.info("Fetching real-time quotes + previous day bars from Twelve Data...")
    quotes = quotes_client.get_quotes(all_tickers)

    watchlist_tickers = _select_watchlist_tickers(
        quotes=quotes,
        ticker_mentions=ticker_mentions,
        target_size=WATCHLIST_SIZE,
    )

    watchlist_quotes = [quotes[ticker] for ticker in watchlist_tickers if ticker in quotes]
    regime_label = _build_regime_label(quotes)
    rates_line = _build_rates_line(quotes.get("TLT"))
    equity_factor_line = _build_equity_factor_line(quotes.get("QQQ"), quotes.get("IWM"))
    volatility_line = _build_volatility_line(quotes.get("VXX"))

    long_ideas, short_ideas = _build_trade_ideas(
        quotes=watchlist_quotes,
        headlines=headlines,
        ticker_mentions=ticker_mentions,
    )

    narrative_context = {
        "report_time": now_local.strftime("%Y-%m-%d %H:%M"),
        "timezone": config.timezone,
        "regime_label": regime_label,
        "rates": rates_line,
        "equity_factor": equity_factor_line,
        "volatility": volatility_line,
        "top_ticker_mentions": [
            {"ticker": ticker, "mentions": mentions}
            for ticker, mentions in sorted(ticker_mentions.items(), key=lambda item: item[1], reverse=True)[:12]
        ],
        "top_headlines": [
            {"source": item.source, "title": item.title, "url": item.url}
            for item in headlines[:10]
        ],
        "watchlist_snapshot": [_build_snapshot_context(snapshot) for snapshot in watchlist_quotes],
    }

    try:
        narrative = generate_narrative_sections(config=config, context=narrative_context)
    except OpenAIClientError as exc:
        logging.warning("OpenAI narrative failed, using fallback narrative: %s", exc)
        narrative = fallback_narrative_sections(narrative_context)

    subject = f"Market Scout | {now_local.strftime('%d %b %Y')} | {regime_label} | Riga 08:30"
    body = _render_email_body(
        generated_at=now_local,
        timezone_name=config.timezone,
        regime_label=regime_label,
        rates_line=rates_line,
        equity_factor_line=equity_factor_line,
        volatility_line=volatility_line,
        narrative=narrative,
        long_ideas=long_ideas,
        short_ideas=short_ideas,
        watchlist_quotes=watchlist_quotes,
        headlines=headlines,
        ticker_mentions=ticker_mentions,
    )

    if args.dry_run:
        print(subject)
        print()
        print(body)
        return 0

    logging.info("Sending report email to %s...", config.email_to)
    try:
        send_plain_email(config=config, subject=subject, body=body)
    except EmailSendError as exc:
        logging.error("SMTP error: %s", exc)
        return 1

    logging.info("Market Scout email sent successfully.")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and send Market Scout email.")
    parser.add_argument("--dry-run", action="store_true", help="Print email to stdout and skip SMTP send.")
    return parser.parse_args()


def _build_trade_ideas(
    quotes: Sequence[QuoteSnapshot],
    headlines: Sequence[Headline],
    ticker_mentions: dict[str, int],
) -> tuple[list[TradeIdea], list[TradeIdea]]:
    if not quotes:
        return [], []

    long_pool = sorted(
        quotes,
        key=lambda item: (
            1 if item.quote_available else 0,
            ticker_mentions.get(item.ticker, 0),
            _pct_change(item.last_price, item.prev_close) or -999.0,
        ),
        reverse=True,
    )

    short_pool = sorted(
        quotes,
        key=lambda item: (
            1 if item.quote_available else 0,
            ticker_mentions.get(item.ticker, 0),
            -(_pct_change(item.last_price, item.prev_close) or 999.0),
        ),
        reverse=True,
    )

    long_ideas: list[TradeIdea] = []
    used: set[str] = set()

    for quote in long_pool:
        if quote.ticker in used:
            continue
        if len(long_ideas) >= 3:
            break

        long_ideas.append(_make_trade_idea(quote, "Long", headlines, ticker_mentions))
        used.add(quote.ticker)

    short_ideas: list[TradeIdea] = []
    for quote in short_pool:
        if quote.ticker in used:
            continue
        if len(short_ideas) >= 3:
            break

        short_ideas.append(_make_trade_idea(quote, "Short", headlines, ticker_mentions))
        used.add(quote.ticker)

    # Ensure minimum 2 ideas per side when possible.
    if len(long_ideas) < 2:
        long_ideas = _top_up_ideas(
            current=long_ideas,
            side="Long",
            source_pool=long_pool,
            already_used=used,
            headlines=headlines,
            ticker_mentions=ticker_mentions,
        )

    if len(short_ideas) < 2:
        short_ideas = _top_up_ideas(
            current=short_ideas,
            side="Short",
            source_pool=short_pool,
            already_used=used,
            headlines=headlines,
            ticker_mentions=ticker_mentions,
        )

    return long_ideas[:4], short_ideas[:4]


def _top_up_ideas(
    current: list[TradeIdea],
    side: str,
    source_pool: Sequence[QuoteSnapshot],
    already_used: set[str],
    headlines: Sequence[Headline],
    ticker_mentions: dict[str, int],
) -> list[TradeIdea]:
    ideas = list(current)
    for quote in source_pool:
        if len(ideas) >= 2:
            break
        if quote.ticker in already_used:
            continue

        ideas.append(_make_trade_idea(quote, side, headlines, ticker_mentions))
        already_used.add(quote.ticker)

    return ideas


def _make_trade_idea(
    quote: QuoteSnapshot,
    side: str,
    headlines: Sequence[Headline],
    ticker_mentions: dict[str, int],
) -> TradeIdea:
    zone = _build_zone(quote=quote, side=side)
    setup_label = zone.setup_label if zone is not None else "Quote unavailable"
    return TradeIdea(
        ticker=quote.ticker,
        side=side,
        setup_label=setup_label,
        quote=quote,
        why_today=_build_why_today(quote.ticker, headlines, ticker_mentions),
        zone=zone,
    )


def _build_zone(quote: QuoteSnapshot, side: str) -> ZonePlan | None:
    if not quote.quote_available:
        return None

    if quote.last_price is None or quote.prev_close is None or quote.prev_high is None or quote.prev_low is None:
        return None

    levels = make_price_levels(
        last_price=quote.last_price,
        prev_close=quote.prev_close,
        prev_high=quote.prev_high,
        prev_low=quote.prev_low,
    )

    if side == "Long":
        return build_long_plan(levels)
    return build_short_plan(levels)


def _build_why_today(ticker: str, headlines: Sequence[Headline], ticker_mentions: dict[str, int]) -> str:
    catalyst = _find_ticker_catalyst(ticker=ticker, headlines=headlines)
    if catalyst:
        return catalyst

    mentions = ticker_mentions.get(ticker, 0)
    if mentions > 0:
        return f"Ticker is active in today's headline flow ({mentions} mentions) with strong liquidity."

    return "Highly liquid name with defined intraday levels against prior close/high/low."


def _find_ticker_catalyst(ticker: str, headlines: Sequence[Headline]) -> str | None:
    pattern = re.compile(rf"(?:\$)?\b{re.escape(ticker)}\b", flags=re.IGNORECASE)

    for item in headlines:
        haystack = f"{item.title} {item.summary}".strip()
        if pattern.search(haystack):
            return _compact_sentence(item.title)

    return None


def _compact_sentence(text: str, max_chars: int = 130) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _select_watchlist_tickers(
    quotes: dict[str, QuoteSnapshot],
    ticker_mentions: dict[str, int],
    target_size: int,
) -> list[str]:
    priority = {ticker: len(TRADE_UNIVERSE) - idx for idx, ticker in enumerate(TRADE_UNIVERSE)}

    ranked = sorted(
        TRADE_UNIVERSE,
        key=lambda ticker: (
            1 if quotes.get(ticker) and quotes[ticker].quote_available else 0,
            ticker_mentions.get(ticker, 0),
            priority[ticker],
        ),
        reverse=True,
    )

    selected = [ticker for ticker in ranked if quotes.get(ticker) and quotes[ticker].quote_available][:target_size]

    if len(selected) < min(8, target_size):
        for ticker in ranked:
            if ticker in selected:
                continue
            selected.append(ticker)
            if len(selected) >= target_size:
                break

    return selected[:target_size]


def _build_regime_label(quotes: dict[str, QuoteSnapshot]) -> str:
    index_changes: list[float] = []
    for ticker in ("SPY", "QQQ", "IWM", "DIA"):
        change = _pct_change_from_snapshot(quotes.get(ticker))
        if change is not None:
            index_changes.append(change)

    if not index_changes:
        return "Balanced"

    breadth = sum(1 for value in index_changes if value > 0) / len(index_changes)
    vxx_change = _pct_change_from_snapshot(quotes.get("VXX"))

    if breadth >= 0.75 and (vxx_change is None or vxx_change <= 0.5):
        return "Risk-On"
    if breadth <= 0.25 and (vxx_change is None or vxx_change >= -0.5):
        return "Risk-Off"
    return "Balanced"


def _build_rates_line(tlt_quote: QuoteSnapshot | None) -> str:
    change = _pct_change_from_snapshot(tlt_quote)
    if change is None:
        return "TLT quote unavailable; rate direction unclear."

    if change >= 0.20:
        return f"Bonds bid (TLT {change:+.2f}% vs prev close), suggesting softer yields."
    if change <= -0.20:
        return f"Bonds offered (TLT {change:+.2f}% vs prev close), suggesting firmer yields."
    return f"Rates broadly stable (TLT {change:+.2f}% vs prev close)."


def _build_equity_factor_line(qqq_quote: QuoteSnapshot | None, iwm_quote: QuoteSnapshot | None) -> str:
    qqq_change = _pct_change_from_snapshot(qqq_quote)
    iwm_change = _pct_change_from_snapshot(iwm_quote)

    if qqq_change is None or iwm_change is None:
        return "Factor read incomplete due to missing QQQ/IWM quote."

    spread = qqq_change - iwm_change
    if spread >= 0.30:
        return f"Growth leading cyclicals (QQQ {qqq_change:+.2f}% vs IWM {iwm_change:+.2f}%)."
    if spread <= -0.30:
        return f"Cyclicals leading growth (IWM {iwm_change:+.2f}% vs QQQ {qqq_change:+.2f}%)."
    return f"Factor leadership mixed (QQQ {qqq_change:+.2f}%, IWM {iwm_change:+.2f}%)."


def _build_volatility_line(vxx_quote: QuoteSnapshot | None) -> str:
    change = _pct_change_from_snapshot(vxx_quote)
    if change is None:
        return "VXX quote unavailable; volatility regime uncertain."

    if change >= 1.50:
        return f"Volatility rising quickly (VXX {change:+.2f}%): tighten risk."
    if change <= -1.50:
        return f"Volatility easing (VXX {change:+.2f}%): supports continuation setups."
    return f"Volatility range-bound (VXX {change:+.2f}%)."


def _render_email_body(
    generated_at: datetime,
    timezone_name: str,
    regime_label: str,
    rates_line: str,
    equity_factor_line: str,
    volatility_line: str,
    narrative: NarrativeSections,
    long_ideas: Sequence[TradeIdea],
    short_ideas: Sequence[TradeIdea],
    watchlist_quotes: Sequence[QuoteSnapshot],
    headlines: Sequence[Headline],
    ticker_mentions: dict[str, int],
) -> str:
    lines: list[str] = []

    lines.append("MARKET SCOUT - Daily Brief")
    lines.append(
        f"Date: {generated_at.strftime('%Y-%m-%d')} ({timezone_name}) | Coverage: US / Europe / Asia | Horizon: intraday"
    )
    lines.append("")

    lines.append("1) TL;DR (today in 20 seconds)")
    for item in narrative.tldr:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("2) Market Pulse (what's moving price)")
    lines.append(f"Risk regime: {regime_label}")
    lines.append(f"Rates: {rates_line}")
    lines.append(f"Equity factor: {equity_factor_line}")
    lines.append(f"Volatility: {volatility_line}")
    lines.append("Key themes:")
    for item in narrative.key_themes:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("3) Trade Board (plan for the day)")
    lines.append("Rule: Only liquid names with catalyst + defined risk.")

    lines.append("A) Long candidates (2-4)")
    if not long_ideas:
        lines.append("- No qualified long setups from available quotes.")
    else:
        for idx, idea in enumerate(long_ideas, start=1):
            _append_trade_idea(lines=lines, idx=idx, idea=idea)

    lines.append("")
    lines.append("B) Short / Fade candidates (2-4)")
    if not short_ideas:
        lines.append("- No qualified short/fade setups from available quotes.")
    else:
        for idx, idea in enumerate(short_ideas, start=1):
            _append_trade_idea(lines=lines, idx=idx, idea=idea)

    lines.append("")
    lines.append("4) Watchlist (8-12, liquid only)")
    watchlist_lines = _render_watchlist_lines(
        watchlist_quotes=watchlist_quotes,
        headlines=headlines,
        ticker_mentions=ticker_mentions,
    )
    lines.extend(watchlist_lines)

    lines.append("")
    lines.append("5) Avoid list (today)")
    for item in narrative.avoid_list:
        lines.append(f"- {item}")

    lines.append("")
    lines.append("6) Risk & Timing")
    lines.append("Risk triggers:")
    for item in narrative.risk_triggers:
        lines.append(f"- {item}")

    lines.append("Today to watch:")
    for item in narrative.timing_watch:
        lines.append(f"- {item}")
    lines.append(f"- Macro calendar: {MACRO_SOURCES[0]}")

    lines.append("")
    lines.append("Sources (links)")
    sources = _build_sources(headlines)
    for idx, link in enumerate(sources, start=1):
        lines.append(f"{idx}) {link}")

    lines.append("")
    lines.append(f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M')} {timezone_name}")

    return "\n".join(lines).strip() + "\n"


def _append_trade_idea(lines: list[str], idx: int, idea: TradeIdea) -> None:
    lines.append(f"{idx}) {idea.ticker} - {idea.setup_label}")
    lines.append(f"   Last: {_format_price(idea.quote.last_price)}")
    lines.append(f"   Why today: {idea.why_today}")

    if idea.zone is None:
        lines.append("   Entry zone: quote unavailable")
        lines.append("   Invalidation: quote unavailable")
        lines.append("   First target: quote unavailable")
        return

    lines.append(f"   Entry zone: {format_range(idea.zone.entry_low, idea.zone.entry_high)}")
    lines.append(f"   Invalidation: {idea.zone.invalidation:.2f}")
    lines.append(f"   First target: {format_target(idea.zone)}")


def _render_watchlist_lines(
    watchlist_quotes: Sequence[QuoteSnapshot],
    headlines: Sequence[Headline],
    ticker_mentions: dict[str, int],
) -> list[str]:
    lines: list[str] = []

    for quote in watchlist_quotes[:WATCHLIST_SIZE]:
        rationale = _build_watchlist_rationale(
            ticker=quote.ticker,
            quote=quote,
            headlines=headlines,
            ticker_mentions=ticker_mentions,
        )
        lines.append(f"- {quote.ticker} - Last: {_format_price(quote.last_price)} - {rationale}")

    if not lines:
        lines.append("- Watchlist unavailable due to missing quote package.")

    return lines


def _build_watchlist_rationale(
    ticker: str,
    quote: QuoteSnapshot,
    headlines: Sequence[Headline],
    ticker_mentions: dict[str, int],
) -> str:
    catalyst = _find_ticker_catalyst(ticker=ticker, headlines=headlines)
    if catalyst:
        return f"Catalyst: {catalyst}"

    change = _pct_change(quote.last_price, quote.prev_close)
    if change is not None:
        direction = "above" if change >= 0 else "below"
        return f"Trading {abs(change):.2f}% {direction} prev close; liquid intraday vehicle."

    mentions = ticker_mentions.get(ticker, 0)
    if mentions > 0:
        return f"Mentioned {mentions}x in today's headlines; monitoring for follow-through."

    return "Liquid large-cap/ETF with stable execution and clear market beta."


def _build_sources(headlines: Sequence[Headline]) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()

    for item in headlines:
        link = item.url.strip()
        if not link or link in seen:
            continue
        links.append(link)
        seen.add(link)
        if len(links) >= 8:
            break

    for macro_link in MACRO_SOURCES:
        if macro_link in seen:
            continue
        links.append(macro_link)
        seen.add(macro_link)

    return links


def _build_snapshot_context(snapshot: QuoteSnapshot) -> dict[str, object]:
    return {
        "ticker": snapshot.ticker,
        "last_price": snapshot.last_price,
        "prev_close": snapshot.prev_close,
        "prev_high": snapshot.prev_high,
        "prev_low": snapshot.prev_low,
        "change_pct": _pct_change(snapshot.last_price, snapshot.prev_close),
        "quote_available": snapshot.quote_available,
    }


def _dedupe_tickers(tickers: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in tickers:
        ticker = raw.upper().strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        out.append(ticker)
    return out


def _pct_change_from_snapshot(snapshot: QuoteSnapshot | None) -> float | None:
    if snapshot is None:
        return None
    return _pct_change(snapshot.last_price, snapshot.prev_close)


def _pct_change(last_price: float | None, prev_close: float | None) -> float | None:
    if last_price is None or prev_close is None or prev_close == 0:
        return None
    return round((last_price / prev_close - 1.0) * 100.0, 2)


def _format_price(value: float | None) -> str:
    if value is None:
        return "quote unavailable"
    return f"{value:.2f}"


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        stream=sys.stdout,
    )


if __name__ == "__main__":
    raise SystemExit(main())

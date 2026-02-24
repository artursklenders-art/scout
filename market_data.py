from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


RSS_SOURCES = [
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("CNBC Markets", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("NYTimes Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
]

CASH_TICKER_PATTERN = re.compile(r"\$([A-Z]{1,5})\b")
EXCHANGE_TICKER_PATTERN = re.compile(r"\b(?:NASDAQ|NYSE|AMEX|TSX|LSE)\s*[:\-]\s*([A-Z]{1,5})\b")


@dataclass(frozen=True)
class Headline:
    source: str
    title: str
    summary: str
    url: str
    published_at: datetime | None


def collect_market_news(
    max_items: int,
    lookback_hours: int,
    timeout_seconds: int,
    user_agent: str,
) -> List[Headline]:
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=lookback_hours)

    all_items: list[Headline] = []
    for source, url in RSS_SOURCES:
        try:
            xml_text = _fetch_text(url=url, timeout_seconds=timeout_seconds, user_agent=user_agent)
            parsed_items = _parse_feed(xml_text=xml_text, source=source)
            logger.info("Fetched %s items from %s", len(parsed_items), source)
            all_items.extend(parsed_items)
        except (URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            logger.warning("Failed to fetch/parse source %s (%s): %s", source, url, exc)

    filtered: list[Headline] = []
    seen_titles: set[str] = set()

    for item in all_items:
        normalized_title = " ".join(item.title.lower().split())
        if not normalized_title or normalized_title in seen_titles:
            continue

        if item.published_at is not None and item.published_at < cutoff:
            continue

        seen_titles.add(normalized_title)
        filtered.append(item)

    filtered.sort(
        key=lambda x: x.published_at if x.published_at is not None else datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    return filtered[:max_items]


def extract_ticker_mentions(headlines: Sequence[Headline], max_items: int = 12) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()

    for item in headlines:
        text = f"{item.title} {item.summary}"
        for ticker in CASH_TICKER_PATTERN.findall(text):
            counter[ticker] += 1
        for ticker in EXCHANGE_TICKER_PATTERN.findall(text):
            counter[ticker] += 1

    return counter.most_common(max_items)


def _fetch_text(url: str, timeout_seconds: int, user_agent: str) -> str:
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=timeout_seconds) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _parse_feed(xml_text: str, source: str) -> list[Headline]:
    root = ET.fromstring(xml_text)

    rss_items = root.findall(".//item")
    if rss_items:
        parsed: list[Headline] = []
        for item in rss_items:
            parsed_item = _parse_rss_item(item, source)
            if parsed_item is not None:
                parsed.append(parsed_item)
        return parsed

    atom_entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    if atom_entries:
        parsed = []
        for entry in atom_entries:
            parsed_item = _parse_atom_entry(entry, source)
            if parsed_item is not None:
                parsed.append(parsed_item)
        return parsed

    return []


def _parse_rss_item(item: ET.Element, source: str) -> Headline | None:
    title = _safe_text(item.find("title"))
    if not title:
        return None

    summary = _safe_text(item.find("description"))
    url = _safe_text(item.find("link"))
    pub_raw = _safe_text(item.find("pubDate"))

    return Headline(
        source=source,
        title=_clean_text(title),
        summary=_clean_text(summary),
        url=url.strip(),
        published_at=_parse_datetime(pub_raw),
    )


def _parse_atom_entry(entry: ET.Element, source: str) -> Headline | None:
    ns = "{http://www.w3.org/2005/Atom}"

    title = _safe_text(entry.find(f"{ns}title"))
    if not title:
        return None

    summary = _safe_text(entry.find(f"{ns}summary"))
    if not summary:
        summary = _safe_text(entry.find(f"{ns}content"))

    url = ""
    link = entry.find(f"{ns}link")
    if link is not None and link.attrib.get("href"):
        url = link.attrib["href"].strip()

    pub_raw = _safe_text(entry.find(f"{ns}published"))
    if not pub_raw:
        pub_raw = _safe_text(entry.find(f"{ns}updated"))

    return Headline(
        source=source,
        title=_clean_text(title),
        summary=_clean_text(summary),
        url=url,
        published_at=_parse_datetime(pub_raw),
    )


def _safe_text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _clean_text(value: str, max_len: int = 280) -> str:
    no_html = re.sub(r"<[^>]+>", " ", value)
    unescaped = html.unescape(no_html)
    compact = re.sub(r"\s+", " ", unescaped).strip()

    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3].rstrip() + "..."


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None

    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass

    iso_candidate = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None

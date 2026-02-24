import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from config import ConfigError, load_config
from email_sender import EmailSendError, send_plain_email
from market_data import collect_market_news, extract_ticker_mentions
from openai_client import OpenAIClientError, generate_market_summary
from prompting import build_prompt, prepare_email_body


def main() -> int:
    _setup_logging()

    try:
        config = load_config()
    except ConfigError as exc:
        logging.error("Configuration error: %s", exc)
        return 1

    now_local = datetime.now(ZoneInfo(config.timezone))
    report_date = now_local.strftime("%Y-%m-%d")

    logging.info("Collecting market news...")
    headlines = collect_market_news(
        max_items=config.news_max_items,
        lookback_hours=config.news_lookback_hours,
        timeout_seconds=config.rss_timeout_seconds,
        user_agent=config.user_agent,
    )

    if not headlines:
        logging.error("Empty market data result. Email will not be sent.")
        return 1

    ticker_mentions = extract_ticker_mentions(headlines)

    prompt = build_prompt(
        headlines=headlines,
        ticker_mentions=ticker_mentions,
        generated_at=now_local,
        timezone_name=config.timezone,
    )

    logging.info("Generating market summary via OpenAI Responses API...")
    try:
        summary = generate_market_summary(config=config, prompt=prompt)
    except OpenAIClientError as exc:
        logging.error("OpenAI API error: %s", exc)
        return 1

    if not summary.strip():
        logging.error("Summary generation returned an empty result. Email will not be sent.")
        return 1

    body = prepare_email_body(
        summary=summary,
        generated_at=now_local,
        timezone_name=config.timezone,
        max_chars=config.email_max_chars,
    )

    subject = f"Обзор рынка — {report_date}"

    logging.info("Sending report email to %s...", config.email_to)
    try:
        send_plain_email(config=config, subject=subject, body=body)
    except EmailSendError as exc:
        logging.error("SMTP error: %s", exc)
        return 1

    logging.info("Report sent successfully.")
    return 0


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        stream=sys.stdout,
    )


if __name__ == "__main__":
    raise SystemExit(main())

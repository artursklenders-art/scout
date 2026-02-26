# Market Scout v2 (Twelve Data + Gmail SMTP + OpenAI Narrative)

Daily plain-text market brief sent by email.

Flow: `news -> Twelve Data prices -> numeric zones -> OpenAI narrative -> Gmail SMTP`.

## Files

- `scout.py` - orchestrator
- `quotes_twelve.py` - Twelve Data quotes (`/price` + `/time_series`), retries, cache
- `zones.py` - numeric entry/invalidation/target calculations
- `openai_client.py` - OpenAI Responses API (narrative sections)
- `email_sender.py` - Gmail SMTP (STARTTLS)
- `config.py` - env loading + validation
- `market_data.py` - RSS news package
- `.env.example` - env template

## Security Rules

- Twelve Data key is read only from `TWELVE_DATA_API_KEY`.
- Secrets are validated at startup in `config.py`.
- `.env` is git-ignored and must never be committed.
- API keys/passwords are never printed to logs.

## Requirements

- Python `3.10+`
- No extra market-data SDK dependency (HTTP via stdlib)

## Environment

Copy template and fill values:

```bash
cp .env.example .env
```

Required:

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `TWELVE_DATA_API_KEY`
- `SMTP_HOST=smtp.gmail.com`
- `SMTP_PORT=587`
- `SMTP_USER=arturs.klenders@gmail.com`
- `SMTP_APP_PASSWORD=<gmail app password>`
- `EMAIL_TO=arturs.klenders@gmail.com`
- `TIMEZONE=Europe/Riga`

Optional tuning:

- `TWELVE_DATA_MAX_RETRIES` (default `3`)
- `TWELVE_DATA_CACHE_TTL_SECONDS` (default `300`)
- `TWELVE_DATA_LOOKBACK_DAYS` (default `60`)
- `TWELVE_DATA_MAX_CREDITS_PER_MINUTE` (default `8`, free-tier safe)

## Run

Dry run (prints subject + body, no SMTP send):

```bash
python3 scout.py --dry-run
```

Live send:

```bash
python3 scout.py
```

## Output Contract

Email format is plain text (no tables) with sections:

1. TL;DR
2. Market Pulse
3. Trade Board (2-4 longs + 2-4 shorts)
4. Watchlist (8-12 liquid names)
5. Avoid list
6. Risk & Timing
7. Sources

Quality guardrails:

- Numeric levels are derived only from Twelve Data fetched levels (`last_price`, `prev_close`, `prev_high`, `prev_low`).
- If quote data is missing for a ticker, numeric zones are omitted and marked `quote unavailable`.
- Price levels are rounded to 2 decimals.

## Scheduling

### GitHub Actions

If you run via `.github/workflows/daily.yml`, add repository secrets:

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `TWELVE_DATA_API_KEY`
- `SMTP_APP_PASSWORD`

### Cron (local)

Weekdays at 08:30 Riga:

```cron
30 8 * * 1-5 cd /path/to/market-scout && /usr/bin/env python3 scout.py >> /tmp/market_scout.log 2>&1
```

## Twelve Data API references

- Price endpoint: [https://twelvedata.com/docs#price](https://twelvedata.com/docs#price)
- Time Series endpoint: [https://twelvedata.com/docs#time-series](https://twelvedata.com/docs#time-series)
- Pricing/limits: [https://twelvedata.com/pricing](https://twelvedata.com/pricing)

## Notes

- Gmail requires 2FA + App Password for SMTP.
- Twelve Data API failures are retried and cached results are reused when fresh.
- A built-in rate limiter keeps Twelve Data requests within configured credits/minute.
- Narrative generation failures fall back to deterministic text, while numeric trade levels remain fully data-driven.

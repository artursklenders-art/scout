# Market Scout (Daily Email via Gmail SMTP)

Automated market digest sender that runs every weekday at 08:30 Europe/Riga and emails a plain-text report to `arturs.klenders@gmail.com`.

Mode: **OpenAI API only (paid)**.
Main schedule mode: **GitHub Actions** (works even when laptop is off).
Also supported: local `cron` on Mac/Linux.

## Project structure

- `scout.py` - orchestration entrypoint
- `market_data.py` - collects raw market/news data from public RSS
- `prompting.py` - prompt construction and output formatting
- `openai_client.py` - OpenAI Responses API call with retry logic
- `email_sender.py` - Gmail SMTP send via STARTTLS
- `config.py` - env loading and validation
- `.github/workflows/daily.yml` - cloud scheduler (default)
- `.env.example` - environment variable template

## 1) Prerequisites

- Python 3.9+
- Gmail account with 2-Step Verification enabled
- Gmail App Password (do not use normal Gmail password)
- OpenAI API key and active API billing

## 2) Security rules

- Never hardcode secrets in code.
- Keep credentials only in `.env` (local) or GitHub Secrets (cloud).
- If App Password is ever exposed, revoke it immediately and create a new one.

## 3) Setup (local)

1. Create project env file:

```bash
cp .env.example .env
```

2. Fill `.env` with real values:

- `OPENAI_API_KEY`
- `OPENAI_MODEL` (example: `gpt-5-mini` or `gpt-5`)
- `SMTP_HOST=smtp.gmail.com`
- `SMTP_PORT=587`
- `SMTP_USER=arturs.klenders@gmail.com`
- `SMTP_APP_PASSWORD=<your Gmail app password>`
- `EMAIL_TO=arturs.klenders@gmail.com`
- `TIMEZONE=Europe/Riga`

3. Run once:

```bash
python scout.py
```

Expected result: summary is generated and a plain-text email is sent.

## 4) Scheduling options

### A) Cloud (default): GitHub Actions

Workflow file is included: `.github/workflows/daily.yml`.

Important:
- GitHub schedule is in UTC.
- Workflow has two UTC triggers (`05:30` and `06:30`) and an internal local-time guard to run only at `08:30 Europe/Riga` (handles DST).

Required GitHub Secrets:
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `SMTP_APP_PASSWORD`

How to add secrets:
- [GitHub docs: Using secrets in GitHub Actions](https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions)

### B) Local: cron (Mac/Linux)

Run on weekdays at 08:30 Riga:

```cron
30 8 * * 1-5 cd /path/to/market-scout && /usr/bin/env python3 scout.py >> /tmp/market_scout.log 2>&1
```

## 5) Output format (email is in Russian)

- `Коротко на сегодня (TL;DR, 3-5 пунктов)`
- `Пульс рынка (5-8 пунктов)`
- `Лист наблюдения на сегодня (8-12)`
- `Сегодня лучше избегать (3-6)`
- `Ключевые события / время`
- Для `Лист наблюдения` и `Сегодня лучше избегать`: компактный формат строк  
  `Тикер | NN/100 | ДА/НЕТ | Почему | Риск`

Quality guards in prompt:
- Keep concise and readable.
- Explicitly mark uncertainty when data is limited.
- Do not invent exact market prices/returns without source evidence.
- Rebound score `NN/100` is mandatory and heuristic (not a guarantee).

## 6) Logs, errors, retries

- Logs go to stdout.
- Clear errors for:
  - SMTP authentication failures
  - OpenAI API failures
  - empty result (email is not sent)
- Retry policy:
  - OpenAI: up to 3 attempts on network/429/5xx
  - SMTP: 1 retry for temporary errors (4xx/disconnect)

## 7) Required external docs

- 2-Step Verification: [Google Help](https://support.google.com/accounts/answer/185839)
- Gmail App Passwords: [Google Help](https://support.google.com/accounts/answer/185833)
- Gmail SMTP settings: [Google SMTP docs](https://support.google.com/a/answer/176600)
- OpenAI API quickstart: [OpenAI docs](https://platform.openai.com/docs/quickstart)

## 8) Notes

- This MVP uses public RSS feeds (no paid market feeds, no logins).
- If data is sparse for the last 24h, the report explicitly marks uncertainty.

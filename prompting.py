from datetime import datetime
from typing import Sequence

from market_data import Headline


def build_prompt(
    headlines: Sequence[Headline],
    ticker_mentions: Sequence[tuple[str, int]],
    generated_at: datetime,
    timezone_name: str,
) -> str:
    headline_block = _render_headlines(headlines)
    tickers_block = _render_tickers(ticker_mentions)

    return f"""Ты дисциплинированный аналитик по рынку.

Задача:
Собери краткий ежедневный рыночный отчет, используя только предоставленный пакет новостей.

Ответ дай строго на русском языке и строго в этом формате (plain text, в этом порядке).
Формат сделай максимально удобным для чтения на телефоне.

Коротко на сегодня (TL;DR, 3-5 пунктов):
- ...

Пульс рынка (5-8 пунктов):
- ...

Лист наблюдения на сегодня (8-12, по убыванию NN):
Тикер | Отскок | Сигнал | Цена входа | Почему сегодня | Риск
TICKER | NN/100 | BUY/SELL | примерная цена или диапазон | кратко | кратко

Сегодня лучше избегать (3-6):
Актив | Отскок | Сигнал | Цена входа | Почему избегать | Риск
ASSET | NN/100 | BUY/SELL | примерная цена или диапазон | кратко | кратко

Ключевые события / время:
- Указывай только если это явно видно в данных. Если нет, напиши: "Нет событий с высокой уверенностью в предоставленных данных."

Требования к качеству:
- Коротко и по делу.
- Если данных мало или есть неопределенность, прямо укажи: "данных мало" или "не подтверждено".
- Для КАЖДОГО пункта в секциях "Лист наблюдения на сегодня" и "Сегодня лучше избегать" обязательно заполняй:
  - "Отскок" как NN/100, где NN — целое число от 1 до 100;
  - "Сигнал" только как BUY или SELL;
  - "Цена входа" как ориентир цены/диапазона на сегодня.
- Если надежной цены во входных данных нет, в поле "Цена входа" пиши: "н/д (нет надежной котировки в пакете)".
- Правило интерпретации для сигнала:
  - если NN >= 60: чаще BUY;
  - если NN <= 59: чаще SELL.
- Этот NN/100 — экспертная эвристическая оценка именно идеи "поиск отскока сегодня", а не факт и не гарантия.
- Не выдумывай рыночные проценты доходности, цены, даты отчетов и другие точные цифры, если их нет во входных данных.
- Если уверенность низкая, все равно дай осторожные гипотезы и пометь неопределенность.
- В табличных секциях не используй длинные абзацы: одна строка = одна идея.

Контекст:
- Время отчета: {generated_at.strftime('%Y-%m-%d %H:%M:%S')} {timezone_name}
- Заголовков в пакете: {len(headlines)}

Подсказки по тикерам из заголовков (опционально):
{tickers_block}

Пакет новостей:
{headline_block}
""".strip()


def prepare_email_body(summary: str, generated_at: datetime, timezone_name: str, max_chars: int) -> str:
    clean_summary = summary.strip()
    suffix = "\n\n[Текст сокращен для соблюдения лимита размера письма]"

    if len(clean_summary) > max_chars:
        allowed = max_chars - len(suffix)
        clean_summary = clean_summary[:allowed].rstrip()

        last_break = clean_summary.rfind("\n")
        if last_break > int(allowed * 0.8):
            clean_summary = clean_summary[:last_break].rstrip()

        clean_summary = clean_summary + suffix

    generated_line = f"Сформировано: {generated_at.strftime('%Y-%m-%d %H:%M:%S')} {timezone_name}"
    return f"{clean_summary}\n\n{generated_line}\n"


def _render_headlines(headlines: Sequence[Headline]) -> str:
    if not headlines:
        return "Нет доступных заголовков."

    lines: list[str] = []
    for idx, item in enumerate(headlines, start=1):
        published = "неизвестно"
        if item.published_at is not None:
            published = item.published_at.strftime("%Y-%m-%d %H:%M UTC")

        lines.append(f"{idx}. [{item.source}] {item.title}")
        if item.summary:
            lines.append(f"   Кратко: {item.summary}")
        if item.url:
            lines.append(f"   Ссылка: {item.url}")
        lines.append(f"   Опубликовано: {published}")

    return "\n".join(lines)


def _render_tickers(ticker_mentions: Sequence[tuple[str, int]]) -> str:
    if not ticker_mentions:
        return "Надежные упоминания тикеров в ленте не обнаружены."

    lines = [f"- {ticker}: упоминаний {count}" for ticker, count in ticker_mentions]
    return "\n".join(lines)

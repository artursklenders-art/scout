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

Ответ дай строго на русском языке и строго в этом формате (plain text, в этом порядке):
Пульс рынка (5-8 пунктов):
- ...

Лист наблюдения на сегодня (8-12):
- TICKER - Отскок сегодня: NN/100 (да/нет) - 1-2 строки: почему важен сегодня / какой катализатор

Сегодня лучше избегать (3-6):
- TICKER/АКТИВ - Отскок сегодня: NN/100 (да/нет) - что лучше не трогать и почему

Ключевые события / время:
- Указывай только если это явно видно в данных. Если нет, напиши: "Нет событий с высокой уверенностью в предоставленных данных."

Требования к качеству:
- Коротко и по делу.
- Если данных мало или есть неопределенность, прямо укажи: "данных мало" или "не подтверждено".
- Для КАЖДОГО пункта в секциях "Лист наблюдения на сегодня" и "Сегодня лучше избегать" обязательно поставь оценку "Отскок сегодня: NN/100 (да/нет)", где NN — целое число от 1 до 100.
- Правило интерпретации: если NN >= 60, ставь "(да)", если NN <= 59, ставь "(нет)".
- Этот NN/100 — экспертная эвристическая оценка именно идеи "поиск отскока сегодня", а не факт и не гарантия.
- Не выдумывай рыночные проценты доходности, цены, даты отчетов и другие точные цифры, если их нет во входных данных.
- Если уверенность низкая, все равно дай осторожные гипотезы и пометь неопределенность.

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

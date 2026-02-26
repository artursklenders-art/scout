from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from config import Config

logger = logging.getLogger(__name__)


class OpenAIClientError(RuntimeError):
    """Raised when the OpenAI API request fails."""


@dataclass(frozen=True)
class NarrativeSections:
    tldr: list[str]
    key_themes: list[str]
    avoid_list: list[str]
    risk_triggers: list[str]
    timing_watch: list[str]


def generate_narrative_sections(config: Config, context: dict[str, Any]) -> NarrativeSections:
    if not config.openai_api_key:
        raise OpenAIClientError("OPENAI_API_KEY is required.")

    prompt = _build_prompt(context)
    payload = {
        "model": config.openai_model,
        "input": prompt,
        "max_output_tokens": 900,
        "reasoning": {"effort": "minimal"},
        "text": {"verbosity": "low"},
    }

    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": f"Bearer {config.openai_api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(1, config.openai_max_retries + 1):
        try:
            response_json = _post_json(
                url=url,
                headers=headers,
                payload=payload,
                timeout_seconds=config.openai_timeout_seconds,
            )
            response_text = _extract_text(response_json)
            if not response_text:
                raise OpenAIClientError("OpenAI API returned an empty response body.")

            parsed = _parse_sections(response_text)
            if parsed is None:
                raise OpenAIClientError("OpenAI API response was not valid JSON in expected schema.")

            return parsed

        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            is_retryable = exc.code == 429 or exc.code >= 500

            if is_retryable and attempt < config.openai_max_retries:
                sleep_for = _backoff_seconds(attempt)
                logger.warning(
                    "OpenAI API error %s (attempt %s/%s). Retrying in %.1fs.",
                    exc.code,
                    attempt,
                    config.openai_max_retries,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue

            if exc.code in (401, 403):
                raise OpenAIClientError(
                    "OpenAI API authentication error. Check OPENAI_API_KEY and OPENAI_MODEL."
                ) from exc

            raise OpenAIClientError(f"OpenAI API error {exc.code}: {body[:400]}") from exc

        except (error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            if attempt < config.openai_max_retries:
                sleep_for = _backoff_seconds(attempt)
                logger.warning(
                    "OpenAI network error (attempt %s/%s): %s. Retrying in %.1fs.",
                    attempt,
                    config.openai_max_retries,
                    exc,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue
            raise OpenAIClientError(f"OpenAI network error after retries: {exc}") from exc

        except json.JSONDecodeError as exc:
            raise OpenAIClientError("OpenAI API returned invalid JSON payload.") from exc

    raise OpenAIClientError("OpenAI request failed after retries.")


def fallback_narrative_sections(context: dict[str, Any]) -> NarrativeSections:
    headlines = context.get("top_headlines", [])
    short_headlines = [item.get("title", "") for item in headlines if isinstance(item, dict)]

    tldr = [
        f"Risk regime sits at {context.get('regime_label', 'Balanced')} with mixed cross-asset signals.",
        "Trade only liquid names and keep risk defined before entry.",
        "Headline flow is active, but dispersion remains elevated intraday.",
    ]

    key_themes = [
        "Index leadership remains concentrated in mega-cap names.",
        "Relative performance between growth and cyclicals is driving tape character.",
        "Bond and volatility proxies should be monitored for regime shifts.",
    ]

    if short_headlines:
        key_themes[0] = f"Headline focus: {short_headlines[0]}"

    avoid_list = [
        "Thinly traded small caps without a clear catalyst.",
        "Names with binary event risk and unclear liquidity.",
        "Setups where invalidation cannot be respected intraday.",
    ]

    risk_triggers = [
        "Sharp upside move in volatility proxies versus prior close.",
        "Failed reclaim of prior close in index ETFs after the open.",
        "Bond reversal that flips equity factor leadership intraday.",
    ]

    timing_watch = [
        "US cash open and first 30 minutes for false-break behavior.",
        "Europe-to-US handoff for momentum continuation or fade.",
        "Final trading hour for trend confirmation versus mean reversion.",
    ]

    return NarrativeSections(
        tldr=tldr,
        key_themes=key_themes,
        avoid_list=avoid_list,
        risk_triggers=risk_triggers,
        timing_watch=timing_watch,
    )


def _build_prompt(context: dict[str, Any]) -> str:
    context_json = json.dumps(context, ensure_ascii=False)

    return (
        "You are a concise market strategist."
        "\nReturn ONLY valid JSON with this exact schema:"
        "\n{"
        '\"tldr\": [\"...\", \"...\", \"...\"],'
        '\"key_themes\": [\"...\", \"...\", \"...\"],'
        '\"avoid_list\": [\"...\", \"...\", \"...\"],'
        '\"risk_triggers\": [\"...\", \"...\", \"...\"],'
        '\"timing_watch\": [\"...\", \"...\", \"...\"]'
        "}"
        "\nRules:"
        "\n- Each item must be one sentence."
        "\n- Do not output markdown, code fences, or extra keys."
        "\n- Do not invent exact prices or earnings dates."
        "\n- Keep each line under 140 characters."
        "\n- Use neutral, actionable language for intraday planning."
        f"\n\nContext JSON:\n{context_json}"
    )


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    req = request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    with request.urlopen(req, timeout=timeout_seconds) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read().decode(charset, errors="replace")
        return json.loads(raw)


def _extract_text(response_json: dict[str, Any]) -> str:
    direct = response_json.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    parts: list[str] = []
    for item in response_json.get("output", []):
        if not isinstance(item, dict):
            continue
        for block in item.get("content", []):
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())

    return "\n".join(parts).strip()


def _parse_sections(response_text: str) -> NarrativeSections | None:
    payload = _parse_json_object(response_text)
    if payload is None:
        return None

    tldr = _normalize_lines(payload.get("tldr"), min_items=3, max_items=5)
    key_themes = _normalize_lines(payload.get("key_themes"), min_items=3, max_items=6)
    avoid_list = _normalize_lines(payload.get("avoid_list"), min_items=3, max_items=6)
    risk_triggers = _normalize_lines(payload.get("risk_triggers"), min_items=3, max_items=6)
    timing_watch = _normalize_lines(payload.get("timing_watch"), min_items=3, max_items=6)

    if not all([tldr, key_themes, avoid_list, risk_triggers, timing_watch]):
        return None

    return NarrativeSections(
        tldr=tldr,
        key_themes=key_themes,
        avoid_list=avoid_list,
        risk_triggers=risk_triggers,
        timing_watch=timing_watch,
    )


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def _normalize_lines(value: Any, min_items: int, max_items: int) -> list[str]:
    if not isinstance(value, list):
        return []

    clean: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        line = " ".join(item.split())
        if not line:
            continue
        clean.append(line[:140])
        if len(clean) >= max_items:
            break

    if len(clean) < min_items:
        return []
    return clean


def _backoff_seconds(attempt: int) -> float:
    base = min(8.0, 2 ** (attempt - 1))
    jitter = random.uniform(0.0, 0.6)
    return base + jitter

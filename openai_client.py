import json
import logging
import random
import time
from urllib import error, request

from config import Config

logger = logging.getLogger(__name__)


class OpenAIClientError(RuntimeError):
    """Raised when the OpenAI API request fails."""


def generate_market_summary(config: Config, prompt: str) -> str:
    if not config.openai_api_key:
        raise OpenAIClientError("OPENAI_API_KEY is required.")

    payload = {
        "model": config.openai_model,
        "input": prompt,
        "max_output_tokens": 1600,
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
            summary = _extract_text(response_json)
            if summary:
                return summary.strip()
            raise OpenAIClientError("OpenAI API returned an empty summary.")

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
            raise OpenAIClientError("OpenAI API returned invalid JSON.") from exc

    raise OpenAIClientError("OpenAI request failed after all retries.")


def _post_json(url: str, headers: dict[str, str], payload: dict, timeout_seconds: int) -> dict:
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


def _extract_text(response_json: dict) -> str:
    direct = response_json.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    chunks: list[str] = []
    for item in response_json.get("output", []):
        if not isinstance(item, dict):
            continue
        for block in item.get("content", []):
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())

    return "\n".join(chunks).strip()


def _backoff_seconds(attempt: int) -> float:
    base = min(8.0, 2 ** (attempt - 1))
    jitter = random.uniform(0.0, 0.5)
    return base + jitter

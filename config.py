import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    """Raised when required environment configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    openai_api_key: str
    openai_model: str
    openai_timeout_seconds: int
    openai_max_retries: int
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_app_password: str
    email_to: str
    timezone: str
    news_lookback_hours: int
    news_max_items: int
    rss_timeout_seconds: int
    email_max_chars: int
    user_agent: str


def load_config(env_path: str = ".env") -> Config:
    _load_dotenv(Path(env_path))

    openai_api_key = _require_env("OPENAI_API_KEY")
    openai_model = _require_env("OPENAI_MODEL")

    smtp_host = _require_env("SMTP_HOST")
    smtp_port = _parse_int("SMTP_PORT", _require_env("SMTP_PORT"))
    smtp_user = _require_env("SMTP_USER")

    raw_smtp_password = _require_env("SMTP_APP_PASSWORD")
    smtp_app_password = raw_smtp_password.replace(" ", "")
    if len(smtp_app_password) < 16:
        raise ConfigError("SMTP_APP_PASSWORD looks invalid. Gmail App Password should be 16 characters.")

    email_to = _require_env("EMAIL_TO")
    timezone = _require_env("TIMEZONE")
    _validate_timezone(timezone)

    openai_timeout_seconds = _parse_int(
        "OPENAI_TIMEOUT_SECONDS",
        os.getenv("OPENAI_TIMEOUT_SECONDS", "60"),
    )
    openai_max_retries = _parse_int(
        "OPENAI_MAX_RETRIES",
        os.getenv("OPENAI_MAX_RETRIES", "3"),
    )
    news_lookback_hours = _parse_int(
        "NEWS_LOOKBACK_HOURS",
        os.getenv("NEWS_LOOKBACK_HOURS", "24"),
    )
    news_max_items = _parse_int(
        "NEWS_MAX_ITEMS",
        os.getenv("NEWS_MAX_ITEMS", "30"),
    )
    rss_timeout_seconds = _parse_int(
        "RSS_TIMEOUT_SECONDS",
        os.getenv("RSS_TIMEOUT_SECONDS", "15"),
    )
    email_max_chars = _parse_int(
        "EMAIL_MAX_CHARS",
        os.getenv("EMAIL_MAX_CHARS", "25000"),
    )

    if smtp_port != 587:
        raise ConfigError("SMTP_PORT must be 587 for STARTTLS in this project.")

    if openai_max_retries < 1:
        raise ConfigError("OPENAI_MAX_RETRIES must be >= 1")

    if news_max_items < 5:
        raise ConfigError("NEWS_MAX_ITEMS must be >= 5")

    if email_max_chars < 5000:
        raise ConfigError("EMAIL_MAX_CHARS must be >= 5000")

    return Config(
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        openai_timeout_seconds=openai_timeout_seconds,
        openai_max_retries=openai_max_retries,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_app_password=smtp_app_password,
        email_to=email_to,
        timezone=timezone,
        news_lookback_hours=news_lookback_hours,
        news_max_items=news_max_items,
        rss_timeout_seconds=rss_timeout_seconds,
        email_max_chars=email_max_chars,
        user_agent=os.getenv(
            "USER_AGENT",
            "MarketScout/1.0 (+https://github.com/your-org/market-scout)",
        ),
    )


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if value and ((value[0] == value[-1]) and value[0] in {'"', "'"}):
            value = value[1:-1]

        if key and key not in os.environ:
            os.environ[key] = value


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if value is None or not value.strip():
        raise ConfigError(f"Missing required environment variable: {key}")
    return value.strip()


def _parse_int(key: str, value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer. Got: {value}") from exc


def _validate_timezone(timezone: str) -> None:
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"Invalid TIMEZONE: {timezone}") from exc

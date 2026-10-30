"""Application configuration loaded from environment variables (and `.env`)."""

from datetime import time
from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class Settings(BaseSettings):
    """All environment-driven settings. Values editable in the admin section only seed defaults."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core
    app_secret_key: str = Field(min_length=16)
    database_url: str = "sqlite:///./data/app.db"
    storage_dir: Path = Path("./data/images")
    messages_file: Path = Path(__file__).parent / "messages.yaml"
    max_upload_mb: int = 10
    session_cookie_secure: bool = True
    log_level: str = "INFO"

    # Phone normalization (ISO 3166 region used when a number has no country prefix)
    default_country_code: str = "MX"
    business_timezone: str = "America/Mexico_City"

    # WhatsApp Cloud API
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_api_version: str = "v21.0"
    whatsapp_api_base_url: str = "https://graph.facebook.com"

    # Ollama (extraction + reply interpretation)
    ollama_url: str = "http://localhost:11434"
    extraction_model: str = "qwen2.5vl:7b"
    reply_model: str = "qwen2.5:7b"
    ollama_timeout_seconds: float = 300.0
    ollama_keep_alive: str = "30m"
    # Long side (px) of the photo sent to the vision model; smaller is faster on CPU.
    extraction_max_image_side: int = 1024

    # Survey flow
    survey_delay_hours: float = 24.0
    confirmation_expiry_hours: float = 24.0
    survey_quiet_period_minutes: int = 120
    free_text_window_minutes: int = 120
    interpretation_window_days: int = 7
    opt_out_threshold: float = 0.5
    rating_threshold: float = 0.8

    # Sending-hours defaults (seed for the admin settings)
    default_sending_days: str = "mon,tue,wed,thu,fri,sat"
    default_sending_start: time = time(9, 0)
    default_sending_end: time = time(18, 0)

    # Worker, retries and rate limits
    worker_tick_seconds: float = 5.0
    send_rate_per_second: float = 1.0
    max_send_attempts: int = 5
    max_extraction_attempts: int = 3
    extraction_retry_base_seconds: int = 5
    max_interpretation_attempts: int = 5
    max_report_attempts: int = 5
    retry_base_seconds: int = 60
    image_retention_days: int = 90

    # Email
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    default_report_day: int = Field(default=1, ge=1, le=28)
    default_report_time: time = time(8, 0)

    # Auth
    employee_session_hours: float = 12.0
    admin_session_minutes: int = 30
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15

    @field_validator("default_sending_days")
    @classmethod
    def _validate_days(cls, value: str) -> str:
        days = [d.strip().lower() for d in value.split(",") if d.strip()]
        unknown = [d for d in days if d not in WEEKDAYS]
        if unknown:
            raise ValueError(f"unknown weekdays: {unknown}")
        return ",".join(days)

    @property
    def sending_days(self) -> list[str]:
        """Default sending weekdays as a list of short names (`mon`..`sun`)."""
        return self.default_sending_days.split(",") if self.default_sending_days else []


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance; exits with a readable message if the configuration is invalid."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = "\n".join(
            f"  - {str(err['loc'][0]).upper()}: {'missing' if err['type'] == 'missing' else err['msg']}"
            for err in exc.errors()
        )
        raise SystemExit(
            f"Invalid configuration:\n{problems}\nCopy .env.example to .env and fill it in (see README)."
        ) from None

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Modelle
    llm_primary: str = "openrouter/nemotron-3-ultra"
    llm_fallbacks: list[str] = Field(default_factory=lambda: ["openrouter/big-pickle"])
    llm_temperature: float = 0.3
    llm_max_tokens: int = 2048
    openrouter_api_key: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_allowed_users: list[int] = Field(default_factory=list)

    # Web
    web_host: str = "0.0.0.0"  # noqa: S104 - bewusst, laeuft hinter Reverse Proxy
    web_port: int = 8080
    web_auth_token: str = ""
    web_cors_origins: list[str] = Field(default_factory=list)

    # Speicher
    memory_db_path: str = "/data/memory/jarvis.db"
    skills_dir: str = "/data/skills"
    audio_dir: str = "/data/audio"
    log_level: str = "INFO"

    # Limits
    max_tool_rounds: int = 12
    max_tool_calls: int = 24
    max_tool_output_chars: int = 8000
    agent_timeout_seconds: int = 300

    session_idle_ttl_hours: int = 72

    # Homelab
    proxmox_host: str = ""
    proxmox_user: str = "root@pam"
    proxmox_token_id: str = ""
    proxmox_token_secret: str = ""
    proxmox_verify_ssl: bool = False

    code_runner_url: str = ""
    code_runner_token: str = ""

    # Sprache
    stt_provider: str = ""
    stt_api_key: str = ""
    stt_model: str = "whisper-large-v3-turbo"
    tts_enabled: bool = True
    tts_voice: str = "de_DE-thorsten-medium"

    @field_validator("llm_fallbacks", "telegram_allowed_users", "web_cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip().startswith("["):
            parts = [p.strip() for p in v.split(",") if p.strip()]
            return parts
        return v

    @property
    def homelab_enabled(self) -> bool:
        return bool(self.proxmox_host and self.proxmox_token_secret)

    @property
    def code_runner_enabled(self) -> bool:
        return bool(self.code_runner_url)

    @property
    def model_chain(self) -> list[str]:
        return [self.llm_primary, *self.llm_fallbacks]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def setup_logging(level: str = "INFO") -> None:
    import logging

    import structlog

    logging.basicConfig(format="%(message)s", level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


log = __import__("structlog").get_logger("jarvis")

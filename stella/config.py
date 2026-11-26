"""Validated, environment-backed configuration for Stella."""
from __future__ import annotations

from pathlib import Path
import os
import re

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_gemini_api_key() -> str:
    """Resolve Gemini credentials using Google's documented env-var precedence."""
    return os.getenv("GOOGLE_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()


class StellaSettings(BaseSettings):
    """Validate only stable process settings; secrets remain outside the model."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=None,
        extra="ignore",
        populate_by_name=True,
    )

    port: int = Field(default=3000, ge=1, le=65535, validation_alias="PORT")
    command_prefix: str = Field(
        default="!", min_length=1, max_length=3, validation_alias="COMMAND_PREFIX"
    )
    sinner_uid: str = Field(default="", validation_alias="SINNER_UID")
    admin_uids: str = Field(
        default="",
        validation_alias=AliasChoices("ADMIN_UIDS", "ADMINUIDS"),
    )
    additional_admin_uids: str = Field(
        default="", validation_alias="ADDITIONAL_ADMIN_UIDS"
    )
    data_dir: Path = Field(default=Path("./data"), validation_alias="DATA_DIR")
    tts_voice: str = Field(
        default="es-CL-CatalinaNeural", validation_alias="TTS_VOICE"
    )
    bot_token: str = Field(default="", validation_alias="ANARCHY_BOT_TOKEN")
    webhook_secret: str = Field(default="", validation_alias="ANARCHY_WEBHOOK_SECRET")
    community_bot_token: str = Field(default="", validation_alias="ANARCHY_COMMUNITY_BOT_TOKEN")
    api_base: str = Field(default="https://api.anarchyapps.org/bot-api/v1", validation_alias="ANARCHY_API_BASE")
    version: str = Field(default="1.0.0", validation_alias="STELLA_VERSION")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "stella.sqlite3"

    @classmethod
    def from_env(cls) -> "StellaSettings":
        return cls()

    @field_validator("command_prefix", mode="before")
    @classmethod
    def normalize_command_prefix(cls, value: object) -> str:
        prefix = str(value or "").strip() or "!"
        if len(prefix) > 3 or any(character.isspace() for character in prefix):
            raise ValueError("COMMAND_PREFIX debe tener 1-3 caracteres sin espacios.")
        return prefix

    @field_validator("tts_voice", mode="before")
    @classmethod
    def normalize_tts_voice(cls, value: object) -> str:
        return str(value or "").strip() or "es-CL-CatalinaNeural"


def _uid_set(value: object) -> frozenset[str]:
    return frozenset(item.strip() for item in re.split(r"[,;\s]+", str(value or "")) if item.strip())


Settings = StellaSettings
SETTINGS = StellaSettings()

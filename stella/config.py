from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _uid_set(value: str) -> frozenset[str]:
    return frozenset(
        item.strip()
        for item in value.replace(";", ",").replace("\n", ",").split(",")
        if item.strip()
    )


@dataclass(frozen=True)
class Settings:
    bot_token: str
    webhook_secret: str
    admin_uids: frozenset[str]
    command_prefix: str
    api_base: str
    database_path: Path
    version: str

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("DATA_DIR", "./data")).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            bot_token=os.getenv("ANARCHY_BOT_TOKEN", "").strip(),
            webhook_secret=os.getenv("ANARCHY_WEBHOOK_SECRET", "").strip(),
            admin_uids=_uid_set(os.getenv("ADMIN_UIDS", "")),
            command_prefix=os.getenv("COMMAND_PREFIX", "!").strip() or "!",
            api_base=os.getenv(
                "ANARCHY_API_BASE", "https://api.anarchyapps.org/bot-api/v1"
            ).rstrip("/"),
            database_path=data_dir / "stella.sqlite3",
            version=os.getenv("RENDER_GIT_COMMIT", "development")[:12],
        )

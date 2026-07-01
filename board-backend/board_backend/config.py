"""Backend settings via environment.

Design principle: no defaults for security-relevant values (``ADMIN_TOKEN``).
If it's missing, the backend fails at startup. Better loud than silently
"empty".
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Loaded from ENV variables (or ``board-backend/.env``)."""

    model_config = SettingsConfigDict(
        env_prefix="JARVIS_BOARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    admin_token: str = Field(
        default="",
        description="Token for /api/v1/identity/register. Required at startup.",
    )
    db_path: Path = Field(
        default=Path("/data/board.db"),
        description="SQLite DB path. The container default is /data/board.db, "
        "overridden locally in tests via tmp_path.",
    )
    register_rate_limit_per_minute: int = Field(
        default=10,
        description="Max requests per IP per minute on /identity/register.",
    )
    replay_window_seconds: int = Field(
        default=300,
        description="Max age of a signed payload (5 min, Plan §C-Sec).",
    )
    bind_host: str = "0.0.0.0"  # noqa: S104 — container bind, behind a reverse proxy
    bind_port: int = 8765

    def require_admin_token(self) -> str:
        if not self.admin_token:
            raise RuntimeError(
                "JARVIS_BOARD_ADMIN_TOKEN is not set — backend startup "
                "refused. Either set it in docker-compose.yml under `environment:` "
                "or inject it via the settings override in tests.",
            )
        return self.admin_token

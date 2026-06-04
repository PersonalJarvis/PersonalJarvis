"""Backend-Settings via Environment.

Designprinzip: keine Defaults fuer sicherheitsrelevante Werte (``ADMIN_TOKEN``).
Wenn er fehlt, faellt das Backend beim Startup ueber. Lieber lautstark als
heimlich „leer".
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Aus ENV-Variablen geladen (oder ``board-backend/.env``)."""

    model_config = SettingsConfigDict(
        env_prefix="JARVIS_BOARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    admin_token: str = Field(
        default="",
        description="Token fuer /api/v1/identity/register. Pflicht zum Start.",
    )
    db_path: Path = Field(
        default=Path("/data/board.db"),
        description="SQLite-DB-Path. Container-Default ist /data/board.db, "
        "lokal in Tests via tmp_path ueberschrieben.",
    )
    register_rate_limit_per_minute: int = Field(
        default=10,
        description="Max Requests pro IP pro Minute auf /identity/register.",
    )
    replay_window_seconds: int = Field(
        default=300,
        description="Max Alter eines signed payloads (5 min, Plan §C-Sec).",
    )
    bind_host: str = "0.0.0.0"  # noqa: S104 — Container-Bind, hinter Reverse-Proxy
    bind_port: int = 8765

    def require_admin_token(self) -> str:
        if not self.admin_token:
            raise RuntimeError(
                "JARVIS_BOARD_ADMIN_TOKEN ist nicht gesetzt — Backend-Start "
                "verweigert. Entweder im docker-compose.yml unter `environment:` "
                "setzen oder beim Test ueber den Settings-Override injecten.",
            )
        return self.admin_token

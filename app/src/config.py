"""Application settings.

Every value below is loaded from `.env` (or the process environment). There
are NO hardcoded defaults — if a key is missing the app will refuse to start
with a Pydantic validation error pointing at the exact field. `.env.example`
is the canonical template; copy it to `.env` and fill in real values.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CameraConfig(BaseSettings):
    """Constructed in-memory from a `Settings` instance — never loaded from
    .env directly, so all fields are required."""

    name: str
    role: Literal["entry", "exit"]
    host: str
    port: int
    channel: int
    username: str
    password: str
    use_https: bool

    @property
    def base_url(self) -> str:
        scheme = "https" if self.use_https else "http"
        return f"{scheme}://{self.host}:{self.port}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    app_name: str
    app_host: str
    app_port: int
    app_debug: bool
    public_listener_url: str

    # ---- Admin auth (web /login form) ----
    secret_key: str
    admin_username: str
    admin_password: str

    # ---- JWT bearer (every API request) ----
    jwt_secret: str
    jwt_algorithm: str
    jwt_expire_minutes: int

    # ---- API docs gate ----
    enable_docs: bool

    # ---- ANPR camera push (/isapi/anpr/{role}) ----
    # Authenticated by ANY of: source IP in CAM*_HOST, X-ANPR-Secret header,
    # or HTTP Basic auth. Empty values disable that particular path.
    anpr_ingest_secret: str
    anpr_ingest_username: str
    anpr_ingest_password: str

    # ---- Postgres ----
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_db: str

    # ---- Camera 1 (entry / outside) ----
    cam1_name: str
    cam1_role: Literal["entry", "exit"]
    cam1_host: str
    cam1_port: int
    cam1_channel: int
    cam1_username: str
    cam1_password: str
    cam1_use_https: bool

    # ---- Camera 2 (exit / inside) ----
    cam2_name: str
    cam2_role: Literal["entry", "exit"]
    cam2_host: str
    cam2_port: int
    cam2_channel: int
    cam2_username: str
    cam2_password: str
    cam2_use_https: bool

    # ---- Telegram ----
    telegram_bot_token: str

    # ---- Alternating-close scheduler ----
    alt_close_interval_seconds: int = Field(ge=1)

    @property
    def sqlalchemy_url_async(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sqlalchemy_url_sync(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def cameras(self) -> list[CameraConfig]:
        return [
            CameraConfig(
                name=self.cam1_name,
                role=self.cam1_role,
                host=self.cam1_host,
                port=self.cam1_port,
                channel=self.cam1_channel,
                username=self.cam1_username,
                password=self.cam1_password,
                use_https=self.cam1_use_https,
            ),
            CameraConfig(
                name=self.cam2_name,
                role=self.cam2_role,
                host=self.cam2_host,
                port=self.cam2_port,
                channel=self.cam2_channel,
                username=self.cam2_username,
                password=self.cam2_password,
                use_https=self.cam2_use_https,
            ),
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()

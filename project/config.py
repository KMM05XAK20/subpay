from pydantic_settings import BaseSettings, SettingsConfigDict
import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

def _req(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"не задана переменная {name} в .env")
    return v

BOT_TOKEN = _req("BOT_TOKEN")
TG_PROXY = os.getenv("TG_PROXY") or None
DB_PATH = os.getenv("DB_PATH", "bot.db")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    tg_proxy: str | None = None
    admin_id: int
    db_url: str
    redis_url: str = "redis://localhost:6379/0"
    requisites: str = "реквизиты не заданы"
    support_username: str = "knaa005"
    max_amount_foreign: float = 1000.0
    quote_ttl_minutes: int = 30
    default_markup_pct: float = 20.0
    max_active_orders: int = 3
    cross_buffer_pct: float = 2.0
    stale_paid_minutes: int = 60      # через сколько напомнить
    ping_repeat_minutes: int = 60     # как часто повторять


settings = Settings()
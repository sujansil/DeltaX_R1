from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "LeaderBoard Puzzles - Round 1"
    environment: str = "development"
    secret_key: str
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    auth_mode: str = "ADMIN_ASSIGNED"
    r1b_device_mode: str = "FOUR_PLAYER_DEVICES"
    media_root: str = "./media"
    media_public_prefix: str = "/media"
    access_token_minutes: int = 720
    activation_token_hours: int = 72
    max_upload_bytes: int = 5 * 1024 * 1024

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

@lru_cache
def get_settings() -> Settings:
    return Settings()

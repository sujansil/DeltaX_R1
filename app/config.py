from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "LeaderBoard Puzzles - Round 1"
    environment: str = "development"
    secret_key: str
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    auth_mode: str = "ADMIN_ASSIGNED"
    media_root: str = "./media"
    media_public_prefix: str = "/media"
    access_token_minutes: int = 720
    player_session_lease_seconds: int = 60
    player_heartbeat_seconds: int = 15
    activation_token_hours: int = 24
    activation_failed_attempt_limit: int = 5
    activation_attempt_window_minutes: int = 15
    activation_lockout_minutes: int = 15
    max_upload_bytes: int = 5 * 1024 * 1024
    r1a_reconciliation_interval_seconds: float = 1.0
    # All four settings AND a matching operator-provisioned database attestation
    # are required. Development/production databases are never resettable.
    r1_reset_enabled: bool = False
    r1_reset_database_name: str = ""
    r1_rehearsal_token: str = ""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

@lru_cache
def get_settings() -> Settings:
    return Settings()

from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Literal


class Settings(BaseSettings):
    # App
    app_name: str = "Portail Socrate"
    environment: Literal["development", "production", "test"] = "development"
    secret_key: str = "change_me_in_production"
    debug: bool = False

    # Base de données
    database_url: str = "postgresql+asyncpg://socrate:socrate_dev@localhost:5432/socrate"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Loge
    lodge_name: str = "Socrate Raison et Progrès"
    lodge_orient: str = "Pont-à-Mousson"
    lodge_obedience: str = "GLNF"
    lodge_domain: str = "amisdesocrate.fr"

    # Email / SMTP
    smtp_from: str = "noreply@amisdesocrate.fr"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_secure: Literal["none", "tls", "ssl"] = "tls"

    # cPanel API
    cpanel_api_url: str = ""
    cpanel_api_token: str = ""

    # URL publique du portail (pour les liens externes, QR codes, emails)
    portal_url: str = ""   # ex: https://staging.amisdesocrate.fr

    # Uploads
    upload_dir: str = "./uploads"
    max_upload_size_mb: int = 20

    # Auth
    access_token_expire_minutes: int = 60 * 8   # 8 heures
    refresh_token_expire_days: int = 30
    algorithm: str = "HS256"

    # WebPush
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_claim_email: str = ""

    # Visio
    visio_provider: str = "jitsi"
    visio_server_url: str = "https://meet.jit.si"
    visio_room_prefix: str = "socrate-"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()

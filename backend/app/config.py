import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-this-secret")
    JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-change-this-jwt-secret")
    DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'club_center.db'}")
    ACCESS_TOKEN_MINUTES = int(os.environ.get("ACCESS_TOKEN_MINUTES", "20"))
    REFRESH_TOKEN_DAYS = int(os.environ.get("REFRESH_TOKEN_DAYS", "7"))
    UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(BASE_DIR / "uploads")))
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    JSON_AS_ASCII = False
    INITIAL_ADMIN_PASSWORD = os.environ.get("INITIAL_ADMIN_PASSWORD", "Admin123!")

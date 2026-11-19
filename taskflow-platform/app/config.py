"""All settings come from environment variables (12-factor), so the same image runs locally and on AWS."""
import os
from urllib.parse import quote


def _bool(value, default):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _database_url(env):
    if env.get("DATABASE_URL"):
        return env["DATABASE_URL"]
    if env.get("DB_HOST"):  # ECS passes the parts separately (password comes from Secrets Manager)
        return "postgresql://{u}:{p}@{h}:{port}/{n}".format(
            u=quote(env.get("DB_USER", "taskflow"), safe=""), p=quote(env.get("DB_PASSWORD", ""), safe=""),
            h=env["DB_HOST"], port=env.get("DB_PORT", "5432"), n=env.get("DB_NAME", "taskflow"))
    return "sqlite:///taskflow.db"


class Config:
    def __init__(self, **overrides):
        env = os.environ
        self.env = env.get("APP_ENV", "development")
        self.database_url = _database_url(env)
        self.jwt_secret = env.get("JWT_SECRET", "")
        self.jwt_expiry_minutes = int(env.get("JWT_EXPIRY_MINUTES", "60"))
        self.stream_token_seconds = int(env.get("STREAM_TOKEN_SECONDS", "60"))
        self.scrypt_n = int(env.get("SCRYPT_N", str(2 ** 14)))
        self.db_pool_max = int(env.get("DB_POOL_MAX", "20"))
        self.cache_ttl = int(env.get("CACHE_TTL_SECONDS", "30"))
        self.cache_max_entries = int(env.get("CACHE_MAX_ENTRIES", "5000"))
        self.event_poll_interval = float(env.get("EVENT_POLL_INTERVAL", "0.5"))
        self.event_retention_hours = int(env.get("EVENT_RETENTION_HOURS", "24"))
        self.start_event_poller = _bool(env.get("START_EVENT_POLLER"), True)
        self.auto_init_db = _bool(env.get("AUTO_INIT_DB"), self.database_url.startswith("sqlite"))
        self.admin_email = env.get("ADMIN_EMAIL", "")
        self.admin_password = env.get("ADMIN_PASSWORD", "")
        self.seed_demo = _bool(env.get("SEED_DEMO"), False)
        for key, value in overrides.items():
            if not hasattr(self, key):
                raise AttributeError(f"unknown config option {key}")
            setattr(self, key, value)
        if not self.jwt_secret:
            if self.env == "production":
                raise RuntimeError("JWT_SECRET must be set when APP_ENV=production")
            self.jwt_secret = "dev-only-secret-change-me-please-32-bytes"
        if len(self.jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET must be at least 32 characters")

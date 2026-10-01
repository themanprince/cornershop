import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

REQUIRED = [
    "BASE_URL",
    "SESSION_SECRET",
    "DATABASE_URL",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_BUCKET",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "ADMIN_EMAILS",
    "PAYSTACK_SECRET_KEY",
    "PAYSTACK_PUBLIC_KEY",
    "BREVO_API_KEY",
    "MAIL_FROM_EMAIL",
]


def _normalize_db_url(url: str) -> str:
    # Supabase hands out postgresql:// URLs; SQLAlchemy needs the psycopg 3 driver named.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@dataclass(frozen=True)
class Settings:
    base_url: str
    session_secret: str
    env: str
    database_url: str
    supabase_url: str
    supabase_service_role_key: str
    supabase_bucket: str
    google_client_id: str
    google_client_secret: str
    admin_emails: frozenset[str]
    paystack_secret_key: str
    paystack_public_key: str
    brevo_api_key: str
    mail_from_email: str
    mail_from_name: str

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def load_settings() -> Settings:
    missing = [name for name in REQUIRED if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
            + ". See .env.example."
        )
    env = os.environ
    return Settings(
        base_url=env["BASE_URL"].rstrip("/"),
        session_secret=env["SESSION_SECRET"],
        env=env.get("ENV", "development"),
        database_url=_normalize_db_url(env["DATABASE_URL"]),
        supabase_url=env["SUPABASE_URL"].rstrip("/"),
        supabase_service_role_key=env["SUPABASE_SERVICE_ROLE_KEY"],
        supabase_bucket=env["SUPABASE_BUCKET"],
        google_client_id=env["GOOGLE_CLIENT_ID"],
        google_client_secret=env["GOOGLE_CLIENT_SECRET"],
        admin_emails=frozenset(
            e.strip().lower() for e in env["ADMIN_EMAILS"].split(",") if e.strip()
        ),
        paystack_secret_key=env["PAYSTACK_SECRET_KEY"],
        paystack_public_key=env["PAYSTACK_PUBLIC_KEY"],
        brevo_api_key=env["BREVO_API_KEY"],
        mail_from_email=env["MAIL_FROM_EMAIL"],
        mail_from_name=env.get("MAIL_FROM_NAME", "Cornershop"),
    )


settings = load_settings()

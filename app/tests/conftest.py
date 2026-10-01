import base64
import json
import os

# config.py fails fast at import, so give it dummy values before the app is imported.
for name, value in {
    "BASE_URL": "http://testserver",
    "SESSION_SECRET": "test-secret",
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/test",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "sb_secret_test",
    "SUPABASE_BUCKET": "bucket",
    "GOOGLE_CLIENT_ID": "client-id",
    "GOOGLE_CLIENT_SECRET": "client-secret",
    "ADMIN_EMAILS": "admin@example.com",
    "PAYSTACK_SECRET_KEY": "sk_test_x",
    "PAYSTACK_PUBLIC_KEY": "pk_test_x",
    "BREVO_API_KEY": "xkeysib-test",
    "MAIL_FROM_EMAIL": "shop@example.com",
}.items():
    os.environ.setdefault(name, value)

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.db import get_db
from app.main import app


class FakeResult:
    def all(self):
        return []


class FakeDB:
    """Stands in for a SQLAlchemy Session: no products, nothing found."""

    def execute(self, *args, **kwargs):
        return None

    def scalars(self, *args, **kwargs):
        return FakeResult()

    def get(self, *args, **kwargs):
        return None


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = lambda: FakeDB()
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


def session_cookie(data: dict) -> str:
    """Build a cookie the way Starlette's SessionMiddleware signs it."""
    payload = base64.b64encode(json.dumps(data).encode())
    return TimestampSigner(os.environ["SESSION_SECRET"]).sign(payload).decode()


def sign_in(client: TestClient, email: str) -> None:
    client.cookies.set("session", session_cookie({"user_id": 1, "email": email, "name": "Test"}))

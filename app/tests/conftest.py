import base64
import json
import os
from pathlib import Path

# Tests that need Postgres run only when TEST_DATABASE_URL is set; the rest use a fake.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# config.py fails fast at import, so give it dummy values before the app is imported.
for name, value in {
    "BASE_URL": "http://testserver",
    "SESSION_SECRET": "test-secret",
    "DATABASE_URL": TEST_DATABASE_URL or "postgresql://user:pass@localhost:5432/test",
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
from sqlalchemy import text

from app.db import SessionLocal, engine, get_db
from app.main import app
from app.models import Product, User

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "schema.sql"

needs_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")


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


@pytest.fixture(scope="session")
def _schema():
    with engine.begin() as conn:
        conn.execute(text("drop table if exists order_items, orders, products, users cascade"))
        conn.exec_driver_sql(SCHEMA_SQL.read_text())


@pytest.fixture
def db(_schema):
    """A real Postgres session on empty tables."""
    with engine.begin() as conn:
        conn.execute(text("truncate order_items, orders, products, users restart identity cascade"))
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def db_client(db):
    """A test client whose routes use the real test database."""
    with TestClient(app, follow_redirects=False) as c:
        yield c


def session_cookie(data: dict) -> str:
    """Build a cookie the way Starlette's SessionMiddleware signs it."""
    payload = base64.b64encode(json.dumps(data).encode())
    return TimestampSigner(os.environ["SESSION_SECRET"]).sign(payload).decode()


# The domain TestClient stores the app's cookies under; using it lets the app's
# Set-Cookie replace ours instead of creating a second "session" cookie.
COOKIE_DOMAIN = "testserver.local"


def sign_in(client: TestClient, email: str, user_id: int = 1, **extra) -> None:
    data = {"user_id": user_id, "email": email, "name": "Test", **extra}
    client.cookies.set("session", session_cookie(data), domain=COOKIE_DOMAIN, path="/")


def read_session(client: TestClient) -> dict:
    cookie = client.cookies.get("session", domain=COOKIE_DOMAIN)
    payload = TimestampSigner(os.environ["SESSION_SECRET"]).unsign(cookie)
    return json.loads(base64.b64decode(payload))


def make_user(db, email: str = "buyer@example.com") -> User:
    user = User(email=email, name="Ada Buyer")
    db.add(user)
    db.commit()
    return user


def make_product(db, name: str = "Rice 5kg", price_kobo: int = 500_000, stock: int = 10, **kw) -> Product:
    product = Product(name=name, price_kobo=price_kobo, stock=stock, **kw)
    db.add(product)
    db.commit()
    return product


def make_order(db, user: User, quantities: dict, status: str = "pending"):
    """Create an order for `user` with {product: qty}, then move it to `status` the way the app would."""
    from fastapi import BackgroundTasks

    from app.services import cart
    from app.services.orders import ShippingDetails, create_pending_order, mark_order_paid

    summary = cart.price_cart({p.id: q for p, q in quantities.items()}, {p.id: p for p in quantities})
    order = create_pending_order(
        db,
        user_id=user.id,
        summary=summary,
        shipping=ShippingDetails(name="Ada Buyer", phone="08012345678", address="1 Marina, Lagos"),
    )
    if status != "pending":
        mark_order_paid(db, order.id, BackgroundTasks())
    if status not in ("pending", "paid"):
        order.status = status  # shipped / delivered / cancelled, for listing tests
        db.commit()
    db.refresh(order)
    return order

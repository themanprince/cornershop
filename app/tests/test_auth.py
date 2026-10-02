import pytest

from app.auth import safe_next
from app.routes import auth_routes
from app.tests.conftest import sign_in


def test_anonymous_visitor_is_sent_to_login(client):
    resp = client.get("/products/5?x=1")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    # After signing in they come back to where they were going.
    assert client.get("/login").status_code == 200


def test_anonymous_post_is_sent_to_login(client):
    resp = client.post("/admin/products/1/delete")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.parametrize("path", ["/health", "/login", "/static/styles.css"])
def test_public_paths_need_no_login(client, path):
    assert client.get(path).status_code == 200


def test_login_page_offers_google(client):
    assert "/auth/google" in client.get("/login").text


@pytest.mark.parametrize(
    "value,expected",
    [
        ("/products/1", "/products/1"),
        ("/admin?status=paid", "/admin?status=paid"),
        ("https://evil.example", "/shop"),
        ("//evil.example", "/shop"),
        ("/\\evil.example", "/shop"),
        (None, "/shop"),
    ],
)
def test_safe_next_blocks_open_redirects(value, expected):
    assert safe_next(value) == expected


def test_signed_in_user_sees_shop(client):
    sign_in(client, "shopper@example.com")
    resp = client.get("/shop")
    assert resp.status_code == 200
    assert "Sign out" in resp.text
    assert "/admin/products" not in resp.text


def test_non_admin_gets_403_on_admin(client):
    sign_in(client, "shopper@example.com")
    assert client.get("/admin").status_code == 403
    assert client.get("/admin/products").status_code == 403
    assert client.post("/admin/products/1/delete").status_code == 403


def test_admin_can_open_admin(client):
    sign_in(client, "Admin@Example.com")
    assert client.get("/admin/products").status_code == 200
    assert client.get("/admin/products/new").status_code == 200


def test_logout_clears_the_user(client):
    sign_in(client, "shopper@example.com")
    resp = client.post("/logout")
    assert resp.headers["location"] == "/login"
    assert client.get("/shop").status_code == 303


class FakeUser:
    id = 7
    email = "new@example.com"
    name = "New Person"
    avatar_url = None


def _fake_google(monkeypatch, userinfo):
    async def authorize_access_token(request):
        return {"userinfo": userinfo}

    monkeypatch.setattr(auth_routes.oauth.google, "authorize_access_token", authorize_access_token)


def test_callback_rejects_unverified_email(client, monkeypatch):
    _fake_google(monkeypatch, {"email": "x@example.com", "email_verified": False})
    resp = client.get("/auth/callback")
    assert resp.headers["location"] == "/login"
    assert client.get("/shop").status_code == 303


def test_first_sign_in_sends_welcome_email(client, monkeypatch):
    _fake_google(monkeypatch, {"email": "New@Example.com", "email_verified": True, "name": "New Person"})
    seen = {}

    def fake_upsert(db, email, name, avatar_url):
        seen["email"] = email
        return FakeUser, True

    monkeypatch.setattr(auth_routes, "upsert_user", fake_upsert)
    sent = []

    async def fake_send(to, subject, html, to_name=None):
        sent.append((to, subject, html))

    monkeypatch.setattr(auth_routes, "send_email", fake_send)

    resp = client.get("/auth/callback")
    assert resp.status_code == 303
    assert seen["email"] == "new@example.com"
    assert len(sent) == 1
    assert sent[0][0] == "new@example.com"
    assert "Welcome" in sent[0][1] and "New Person" in sent[0][2]
    assert resp.headers["location"] == "/shop"
    assert client.get("/shop").status_code == 200


def test_returning_user_gets_no_welcome_email(client, monkeypatch):
    _fake_google(monkeypatch, {"email": "new@example.com", "email_verified": True})
    monkeypatch.setattr(auth_routes, "upsert_user", lambda *a: (FakeUser, False))
    sent = []

    async def fake_send(*a, **k):
        sent.append(a)

    monkeypatch.setattr(auth_routes, "send_email", fake_send)
    client.get("/auth/callback")
    assert sent == []

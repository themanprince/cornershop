import pytest

from app.tests.conftest import sign_in


@pytest.mark.parametrize("path", ["/", "/privacy", "/terms"])
def test_public_pages_need_no_login(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert 'href="/privacy"' in resp.text and 'href="/terms"' in resp.text


def test_landing_page_explains_the_app_and_google_data_use(client):
    page = client.get("/").text
    assert "Sign in with Google" in page
    assert "name, email address and profile picture" in page


def test_privacy_policy_covers_google_user_data(client):
    page = client.get("/privacy").text
    assert "Google API Services User Data Policy" in page
    assert "Limited Use" in page
    for provider in ("Paystack", "Brevo", "Supabase", "Render"):
        assert provider in page
    assert "shop@example.com" in page  # contact address defaults to MAIL_FROM_EMAIL


def test_signed_in_landing_page_links_to_shop(client):
    sign_in(client, "shopper@example.com")
    assert 'href="/shop"' in client.get("/").text


def test_site_verification_meta_tag_only_when_configured(client, monkeypatch):
    from app import templating

    assert "google-site-verification" not in client.get("/").text
    monkeypatch.setitem(templating.templates.env.globals, "google_site_verification", "abc123")
    assert '<meta name="google-site-verification" content="abc123">' in client.get("/").text


def test_empty_shop_shows_welcome_and_placeholders(client):
    sign_in(client, "shopper@example.com")
    page = client.get("/shop").text
    assert "Welcome back, Test" in page
    assert "Secure checkout" in page
    assert page.count("placeholder-card") >= 4


def test_admin_sees_add_product_prompt_on_empty_shop(client):
    sign_in(client, "admin@example.com")
    assert 'href="/admin/products/new"' in client.get("/shop").text


def test_pwa_manifest_and_sw_accessible(client):
    manifest_resp = client.get("/manifest.json")
    assert manifest_resp.status_code == 200
    data = manifest_resp.json()
    assert data["name"] == "Cornershop"
    assert data["display"] == "standalone"

    sw_resp = client.get("/sw.js")
    assert sw_resp.status_code == 200
    assert "cornershop" in sw_resp.text


def test_pwa_meta_tags_present(client):
    page = client.get("/").text
    assert 'rel="manifest" href="/manifest.json"' in page
    assert 'name="apple-mobile-web-app-capable" content="yes"' in page
    assert "apple-touch-icon" in page


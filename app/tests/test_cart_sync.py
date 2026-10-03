"""The cart belongs to the account, so every device the shopper signs in on sees the same cart."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.tests.conftest import make_product, make_user, needs_db, read_session, sign_in

pytestmark = needs_db


def signed_in_device(user) -> TestClient:
    """A browser with its own cookie jar, e.g. the desktop site or the installed phone app."""
    device = TestClient(app, follow_redirects=False)
    sign_in(device, user.email, user_id=user.id)
    return device


@pytest.fixture
def buyer(db):
    return make_user(db)


@pytest.fixture
def laptop(buyer):
    with signed_in_device(buyer) as device:
        yield device


@pytest.fixture
def phone(buyer):
    with signed_in_device(buyer) as device:
        yield device


def test_item_added_on_one_device_is_in_the_cart_on_another(db, laptop, phone):
    rice = make_product(db, "Rice 5kg", price_kobo=500_000)
    laptop.post("/cart/add", data={"product_id": rice.id, "qty": 2})

    page = phone.get("/cart").text
    assert "Rice 5kg" in page and "₦10,000.00" in page
    assert 'data-cart-count="2"' in phone.get("/shop").text


def test_changes_on_either_device_show_on_the_other(db, laptop, phone):
    rice, beans = make_product(db, "Rice"), make_product(db, "Beans")
    laptop.post("/cart/add", data={"product_id": rice.id, "qty": 2})
    phone.post("/cart/add", data={"product_id": beans.id, "qty": 1})
    phone.post("/cart/update", data={"product_id": rice.id, "qty": 0})

    assert 'data-cart-count="1"' in laptop.get("/shop").text
    page = laptop.get("/cart").text
    assert "Beans" in page and "Rice" not in page


def test_another_users_cart_is_not_shared(db, laptop):
    rice = make_product(db)
    laptop.post("/cart/add", data={"product_id": rice.id, "qty": 2})

    with signed_in_device(make_user(db, "stranger@example.com")) as stranger:
        assert 'data-cart-count="0"' in stranger.get("/shop").text


def test_error_pages_show_the_cart_badge_without_leaking_a_db_connection(db, laptop):
    rice = make_product(db)
    laptop.post("/cart/add", data={"product_id": rice.id, "qty": 2})

    resp = laptop.get("/products/999999")

    assert resp.status_code == 404 and 'data-cart-count="2"' in resp.text
    stuck = db.scalar(
        text(
            "select count(*) from pg_stat_activity"
            " where state = 'idle in transaction' and pid <> pg_backend_pid()"
        )
    )
    assert stuck == 0


def test_cart_is_not_kept_in_the_cookie(db, laptop):
    rice = make_product(db)
    laptop.post("/cart/add", data={"product_id": rice.id, "qty": 2})
    assert "cart" not in read_session(laptop)

from types import SimpleNamespace

import pytest

from app.services import cart
from app.tests.conftest import make_product, make_user, needs_db


def product(id=1, name="Rice", price_kobo=1000, stock=5, is_active=True):
    return SimpleNamespace(id=id, name=name, price_kobo=price_kobo, stock=stock, is_active=is_active)


@pytest.fixture
def shopper(db):
    return make_user(db, "shopper@example.com")


@needs_db
def test_new_user_has_empty_cart(db, shopper):
    assert cart.get_cart(db, shopper.id) == {}
    assert cart.item_count(db, shopper.id) == 0


@needs_db
def test_add_item_accumulates_and_caps_at_stock(db, shopper):
    rice = make_product(db, stock=5)
    assert cart.add_item(db, shopper.id, rice.id, 2, stock=5) == 2
    assert cart.add_item(db, shopper.id, rice.id, 2, stock=5) == 4
    assert cart.add_item(db, shopper.id, rice.id, 9, stock=5) == 5
    assert cart.get_cart(db, shopper.id) == {rice.id: 5}
    assert cart.item_count(db, shopper.id) == 5


@needs_db
def test_add_item_with_no_stock_adds_nothing(db, shopper):
    rice = make_product(db, stock=0)
    assert cart.add_item(db, shopper.id, rice.id, 1, stock=0) == 0
    assert cart.get_cart(db, shopper.id) == {}


@needs_db
def test_set_quantity_replaces_caps_and_removes_on_zero(db, shopper):
    rice = make_product(db)
    cart.add_item(db, shopper.id, rice.id, 1, stock=5)
    assert cart.set_quantity(db, shopper.id, rice.id, 3, stock=5) == 3
    assert cart.set_quantity(db, shopper.id, rice.id, 50, stock=5) == 5
    assert cart.set_quantity(db, shopper.id, rice.id, 0, stock=5) == 0
    assert cart.get_cart(db, shopper.id) == {}


@needs_db
def test_negative_quantity_is_treated_as_zero(db, shopper):
    rice, beans = make_product(db, "Rice"), make_product(db, "Beans")
    cart.add_item(db, shopper.id, rice.id, 2, stock=5)
    assert cart.set_quantity(db, shopper.id, rice.id, -3, stock=5) == 0
    assert cart.add_item(db, shopper.id, beans.id, -1, stock=5) == 0
    assert cart.get_cart(db, shopper.id) == {}


@needs_db
def test_clear_cart_empties_only_that_users_cart(db, shopper):
    other = make_user(db, "other@example.com")
    rice = make_product(db)
    cart.add_item(db, shopper.id, rice.id, 1, stock=5)
    cart.add_item(db, other.id, rice.id, 2, stock=5)

    cart.clear_cart(db, shopper.id)

    assert cart.get_cart(db, shopper.id) == {}
    assert cart.get_cart(db, other.id) == {rice.id: 2}


def test_price_cart_uses_database_prices():
    summary = cart.price_cart({1: 2, 2: 1}, {1: product(1, price_kobo=1500), 2: product(2, price_kobo=250)})
    assert summary.total_kobo == 3250
    assert [line.line_total_kobo for line in summary.lines] == [3000, 250]
    assert summary.problems == []
    assert summary.ok


def test_price_cart_flags_unavailable_products():
    summary = cart.price_cart({1: 1, 2: 1}, {1: product(1, is_active=False)})
    assert summary.lines == []
    assert summary.total_kobo == 0
    assert len(summary.problems) == 2
    assert not summary.ok


def test_price_cart_flags_quantity_above_stock():
    summary = cart.price_cart({1: 4}, {1: product(1, name="Beans", stock=3)})
    assert not summary.ok
    assert "Beans" in summary.problems[0]
    assert len(summary.lines) == 1  # still shown so the shopper can fix it


def test_empty_cart_is_not_ok_for_checkout():
    assert not cart.price_cart({}, {}).ok

from types import SimpleNamespace

from app.services import cart


def product(id=1, name="Rice", price_kobo=1000, stock=5, is_active=True):
    return SimpleNamespace(id=id, name=name, price_kobo=price_kobo, stock=stock, is_active=is_active)


def test_empty_session_has_empty_cart():
    assert cart.get_cart({}) == {}
    assert cart.item_count({}) == 0


def test_add_item_accumulates_and_caps_at_stock():
    session = {}
    assert cart.add_item(session, 1, 2, stock=5) == 2
    assert cart.add_item(session, 1, 2, stock=5) == 4
    assert cart.add_item(session, 1, 9, stock=5) == 5
    assert cart.get_cart(session) == {1: 5}
    assert cart.item_count(session) == 5


def test_add_item_with_no_stock_adds_nothing():
    session = {}
    assert cart.add_item(session, 1, 1, stock=0) == 0
    assert cart.get_cart(session) == {}


def test_session_stores_only_ids_and_quantities_as_json_safe_values():
    session = {}
    cart.add_item(session, 7, 1, stock=5)
    assert session["cart"] == {"7": 1}


def test_set_quantity_replaces_caps_and_removes_on_zero():
    session = {}
    cart.add_item(session, 1, 1, stock=5)
    assert cart.set_quantity(session, 1, 3, stock=5) == 3
    assert cart.set_quantity(session, 1, 50, stock=5) == 5
    assert cart.set_quantity(session, 1, 0, stock=5) == 0
    assert cart.get_cart(session) == {}


def test_negative_quantity_is_treated_as_zero():
    session = {}
    cart.add_item(session, 1, 2, stock=5)
    assert cart.set_quantity(session, 1, -3, stock=5) == 0
    assert cart.add_item(session, 2, -1, stock=5) == 0
    assert cart.get_cart(session) == {}


def test_tampered_session_values_are_ignored():
    session = {"cart": {"1": 2, "x": 1, "3": "many", "4": 0, "5": -2}}
    assert cart.get_cart(session) == {1: 2}


def test_clear_cart():
    session = {}
    cart.add_item(session, 1, 1, stock=5)
    cart.clear_cart(session)
    assert cart.get_cart(session) == {}


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

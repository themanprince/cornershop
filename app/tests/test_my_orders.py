from app.tests.conftest import make_order, make_product, make_user, needs_db, sign_in

pytestmark = needs_db


def test_my_orders_lists_only_my_orders(db, db_client):
    me = make_user(db, "me@example.com")
    other = make_user(db, "other@example.com")
    rice = make_product(db, "Rice")
    mine = make_order(db, me, {rice: 1}, "shipped")
    theirs = make_order(db, other, {rice: 1}, "paid")
    sign_in(db_client, me.email, user_id=me.id)

    page = db_client.get("/orders").text
    assert f"Order #{mine.id}" in page and "Shipped" in page
    assert f"Order #{theirs.id}" not in page


def test_my_order_detail_is_owner_only(db, db_client):
    me = make_user(db, "me@example.com")
    other = make_user(db, "other@example.com")
    rice = make_product(db, "Rice 5kg")
    mine = make_order(db, me, {rice: 2}, "paid")
    theirs = make_order(db, other, {rice: 1}, "paid")
    sign_in(db_client, me.email, user_id=me.id)

    page = db_client.get(f"/orders/{mine.id}").text
    assert "Rice 5kg" in page and "1 Marina, Lagos" in page
    assert db_client.get(f"/orders/{theirs.id}").status_code == 404
    assert db_client.get("/orders/999").status_code == 404


def test_unpaid_order_is_shown_as_payment_not_completed(db, db_client):
    me = make_user(db, "me@example.com")
    order = make_order(db, me, {make_product(db): 1}, "pending")
    sign_in(db_client, me.email, user_id=me.id)
    assert "Payment not completed" in db_client.get(f"/orders/{order.id}").text


def test_empty_order_history(db, db_client):
    me = make_user(db, "me@example.com")
    sign_in(db_client, me.email, user_id=me.id)
    page = db_client.get("/orders").text
    assert "You haven't placed any orders yet" in page


def test_my_orders_requires_sign_in(db, db_client):
    assert db_client.get("/orders").headers["location"] == "/login"


def test_navbar_links_to_my_orders(client):
    sign_in(client, "x@example.com")
    assert 'href="/orders"' in client.get("/shop").text

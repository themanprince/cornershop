import html

import pytest
from sqlalchemy import select

from app.models import Order, Product
from app.tests.conftest import make_order, make_product, make_user, needs_db, sign_in

pytestmark = needs_db


@pytest.fixture
def emails(monkeypatch):
    sent = []

    async def fake_send(to, subject, html, to_name=None):
        sent.append({"to": to, "subject": subject, "html": html})

    monkeypatch.setattr("app.services.orders.send_email", fake_send)
    return sent


@pytest.fixture
def admin(db, db_client):
    user = make_user(db, "admin@example.com")
    sign_in(db_client, user.email, user_id=user.id)
    return user


@pytest.fixture
def buyer(db):
    return make_user(db, "buyer@example.com")


def status_of(db, order) -> str:
    db.expire_all()
    return db.get(Order, order.id).status


def stock_of(db, product) -> int:
    db.expire_all()
    return db.get(Product, product.id).stock


def set_status(client, order, status):
    return client.post(f"/admin/orders/{order.id}/status", data={"status": status})


# ---- access --------------------------------------------------------------------


def test_non_admin_cannot_see_or_change_orders(db, db_client, buyer):
    order = make_order(db, buyer, {make_product(db): 1}, "paid")
    sign_in(db_client, buyer.email, user_id=buyer.id)
    assert db_client.get("/admin").status_code == 403
    assert db_client.get("/admin/orders").status_code == 403
    assert db_client.get(f"/admin/orders/{order.id}").status_code == 403
    assert set_status(db_client, order, "shipped").status_code == 403
    assert status_of(db, order) == "paid"


# ---- list and detail -----------------------------------------------------------


def test_orders_list_is_newest_first_and_filterable(db, db_client, admin, buyer):
    rice = make_product(db, "Rice")
    first = make_order(db, buyer, {rice: 1}, "paid")
    second = make_order(db, buyer, {rice: 2}, "pending")

    page = db_client.get("/admin/orders").text
    assert page.index(f"#{second.id}") < page.index(f"#{first.id}")
    assert "buyer@example.com" in page

    paid_only = db_client.get("/admin/orders?status=paid").text
    assert f"#{first.id}" in paid_only and f"#{second.id}" not in paid_only

    # An unknown filter shows everything instead of failing.
    assert f"#{second.id}" in db_client.get("/admin/orders?status=nonsense").text


def test_order_detail_shows_everything_needed_to_deliver(db, db_client, admin, buyer):
    order = make_order(db, buyer, {make_product(db, "Rice 5kg", price_kobo=500_000): 2}, "paid")
    page = db_client.get(f"/admin/orders/{order.id}").text
    for text in ("Rice 5kg", "₦10,000.00", "Ada Buyer", "08012345678", "1 Marina, Lagos",
                 "buyer@example.com", order.paystack_reference):
        assert text in page
    # Only the allowed next steps are offered.
    assert 'value="shipped"' in page and 'value="cancelled"' in page
    assert 'value="delivered"' not in page
    assert page.index('value="shipped"') < page.index('value="cancelled"')  # forward step first


def test_missing_order_is_404(db, db_client, admin):
    assert db_client.get("/admin/orders/999").status_code == 404
    assert db_client.post("/admin/orders/999/status", data={"status": "shipped"}).status_code == 404


def test_oversold_flag_is_shown(db, db_client, admin, buyer):
    oil = make_product(db, "Oil", stock=5)
    order = make_order(db, buyer, {oil: 3})
    oil.stock = 1
    db.commit()
    from fastapi import BackgroundTasks

    from app.services.orders import mark_order_paid

    mark_order_paid(db, order.id, BackgroundTasks())
    assert "[OVERSOLD]" in db_client.get(f"/admin/orders/{order.id}").text


# ---- status changes ------------------------------------------------------------


def test_ship_then_deliver_emails_the_customer_each_time(db, db_client, admin, buyer, emails):
    order = make_order(db, buyer, {make_product(db): 1}, "paid")

    resp = set_status(db_client, order, "shipped")
    assert resp.status_code == 303 and resp.headers["location"] == f"/admin/orders/{order.id}"
    assert status_of(db, order) == "shipped"

    set_status(db_client, order, "delivered")
    assert status_of(db, order) == "delivered"

    assert [(e["to"], e["subject"]) for e in emails] == [
        ("buyer@example.com", f"Order #{order.id} is on its way"),
        ("buyer@example.com", f"Order #{order.id} has been delivered"),
    ]


def test_disallowed_change_is_refused(db, db_client, admin, buyer, emails):
    order = make_order(db, buyer, {make_product(db): 1}, "pending")
    resp = set_status(db_client, order, "shipped")
    assert resp.status_code == 303
    assert status_of(db, order) == "pending"
    assert emails == []
    assert "can't be changed" in html.unescape(db_client.get(f"/admin/orders/{order.id}").text)


def test_cancelling_a_paid_order_restocks_and_promises_a_refund(db, db_client, admin, buyer, emails):
    rice = make_product(db, stock=10)
    order = make_order(db, buyer, {rice: 3}, "paid")
    assert stock_of(db, rice) == 7

    set_status(db_client, order, "cancelled")

    assert status_of(db, order) == "cancelled"
    assert stock_of(db, rice) == 10
    assert emails[0]["subject"] == f"Order #{order.id} has been cancelled"
    assert "refund" in emails[0]["html"]
    assert "Refund" in db_client.get(f"/admin/orders/{order.id}").text  # reminder for the admin


def test_cancelling_an_oversold_order_does_not_restock(db, db_client, admin, buyer, emails):
    oil = make_product(db, "Oil", stock=5)
    order = make_order(db, buyer, {oil: 3})
    oil.stock = 1
    db.commit()
    from fastapi import BackgroundTasks

    from app.services.orders import mark_order_paid

    mark_order_paid(db, order.id, BackgroundTasks())
    set_status(db_client, order, "cancelled")

    assert status_of(db, order) == "cancelled"
    assert stock_of(db, oil) == 1
    db.expire_all()
    assert "restore stock manually" in db.get(Order, order.id).admin_note


def test_cancelling_an_unpaid_order_sends_no_email(db, db_client, admin, buyer, emails):
    rice = make_product(db, stock=10)
    order = make_order(db, buyer, {rice: 2}, "pending")
    set_status(db_client, order, "cancelled")
    assert status_of(db, order) == "cancelled"
    assert stock_of(db, rice) == 10
    assert emails == []


def test_stale_form_cannot_override_a_newer_status(db, db_client, admin, buyer, emails):
    """Two admins open the same paid order; one ships it, the other then tries to cancel."""
    order = make_order(db, buyer, {make_product(db): 1}, "paid")
    set_status(db_client, order, "shipped")
    set_status(db_client, order, "cancelled")
    assert status_of(db, order) == "shipped"
    assert len(emails) == 1


# ---- overview ------------------------------------------------------------------


def test_overview_summarises_the_shop(db, db_client, admin, buyer):
    rice = make_product(db, "Rice", price_kobo=500_000, stock=20)
    make_product(db, "Nearly gone beans", stock=2)
    make_order(db, buyer, {rice: 2}, "paid")
    make_order(db, buyer, {rice: 1}, "delivered")
    make_order(db, buyer, {rice: 1}, "pending")

    page = db_client.get("/admin").text
    assert 'data-stat="to-ship">1<' in page
    assert 'data-stat="awaiting-payment">1<' in page
    assert 'data-stat="revenue">₦15,000.00<' in page
    assert "Nearly gone beans" in page


def test_overview_with_no_data(db, db_client, admin):
    page = db_client.get("/admin").text
    assert 'data-stat="to-ship">0<' in page
    assert "No orders yet" in page


# ---- paid after cancel ---------------------------------------------------------


def test_payment_arriving_after_cancel_is_flagged_once_and_customer_told(db, db_client, admin, buyer, emails):
    from fastapi import BackgroundTasks

    from app.services.orders import mark_order_paid

    rice = make_product(db, stock=10)
    order = make_order(db, buyer, {rice: 2}, "pending")
    set_status(db_client, order, "cancelled")

    assert mark_order_paid(db, order.id, tasks := BackgroundTasks()) is False
    assert mark_order_paid(db, order.id, tasks) is False  # webhook and callback both arrive

    db.expire_all()
    fresh = db.get(Order, order.id)
    assert fresh.status == "cancelled"
    assert fresh.admin_note.count("[PAID AFTER CANCEL]") == 1
    assert stock_of(db, rice) == 10
    assert len(tasks.tasks) == 1
    assert "refund" in tasks.tasks[0].args[2]
    assert "[PAID AFTER CANCEL]" in db_client.get("/admin").text

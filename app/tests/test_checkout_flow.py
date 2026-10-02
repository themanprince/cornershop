import hashlib
import hmac
import json

import pytest
from sqlalchemy import select

from app.models import Order, Product
from app.services import payments
from app.tests.conftest import make_product, make_user, needs_db, read_session, sign_in

pytestmark = needs_db

SHIPPING_FORM = {"shipping_name": "Ada Buyer", "shipping_phone": "0801 234 5678", "shipping_address": "1 Marina, Lagos"}


class FakePaystack:
    """Replaces the Paystack HTTP calls; `paid` maps reference -> amount actually charged."""

    def __init__(self):
        self.initialized = []
        self.paid = {}

    async def initialize_transaction(self, *, email, amount_kobo, reference, callback_url):
        self.initialized.append(
            {"email": email, "amount": amount_kobo, "reference": reference, "callback_url": callback_url}
        )
        return f"https://checkout.paystack.com/{reference}"

    async def verify_transaction(self, reference):
        if reference not in self.paid:
            return {"status": "abandoned", "reference": reference, "amount": 0, "currency": "NGN"}
        return {"status": "success", "reference": reference, "amount": self.paid[reference], "currency": "NGN"}


@pytest.fixture
def paystack(monkeypatch):
    fake = FakePaystack()
    monkeypatch.setattr(payments, "initialize_transaction", fake.initialize_transaction)
    monkeypatch.setattr(payments, "verify_transaction", fake.verify_transaction)
    return fake


@pytest.fixture
def emails(monkeypatch):
    sent = []

    async def fake_send(to, subject, html, to_name=None):
        sent.append((to, subject))

    monkeypatch.setattr("app.services.orders.send_email", fake_send)
    return sent


@pytest.fixture
def buyer(db, db_client):
    user = make_user(db)
    sign_in(db_client, user.email, user_id=user.id)
    return user


def checkout(client, form=SHIPPING_FORM):
    return client.post("/checkout", data=form)


def only_order(db) -> Order:
    db.expire_all()
    return db.scalars(select(Order)).one()


# ---- cart ----------------------------------------------------------------------


def test_add_to_cart_caps_quantity_at_stock(db, db_client, buyer):
    rice = make_product(db, stock=3)
    resp = db_client.post("/cart/add", data={"product_id": rice.id, "qty": 10})
    assert resp.status_code == 303 and resp.headers["location"] == "/cart"
    assert read_session(db_client)["cart"] == {str(rice.id): 3}


def test_cannot_add_inactive_product(db, db_client, buyer):
    hidden = make_product(db, is_active=False)
    assert db_client.post("/cart/add", data={"product_id": hidden.id, "qty": 1}).status_code == 404


def test_cart_page_shows_database_prices(db, db_client, buyer):
    rice = make_product(db, "Rice 5kg", price_kobo=500_000)
    db_client.post("/cart/add", data={"product_id": rice.id, "qty": 2})
    page = db_client.get("/cart").text
    assert "Rice 5kg" in page and "₦10,000.00" in page


def test_update_cart_to_zero_removes_item(db, db_client, buyer):
    rice = make_product(db)
    db_client.post("/cart/add", data={"product_id": rice.id, "qty": 2})
    db_client.post("/cart/update", data={"product_id": rice.id, "qty": 0})
    assert read_session(db_client).get("cart") == {}


def test_navbar_shows_cart_count(db, db_client, buyer):
    rice = make_product(db)
    db_client.post("/cart/add", data={"product_id": rice.id, "qty": 2})
    assert 'data-cart-count="2"' in db_client.get("/shop").text


# ---- checkout ------------------------------------------------------------------


def test_checkout_with_empty_cart_goes_back_to_cart(db, db_client, buyer, paystack):
    assert db_client.get("/checkout").headers["location"] == "/cart"
    assert checkout(db_client).headers["location"] == "/cart"
    assert paystack.initialized == []


def test_checkout_creates_pending_order_and_redirects_to_paystack(db, db_client, buyer, paystack):
    rice = make_product(db, price_kobo=500_000, stock=5)
    db_client.post("/cart/add", data={"product_id": rice.id, "qty": 2})

    # A tampered price in the form is ignored; the server recomputes from the database.
    resp = checkout(db_client, {**SHIPPING_FORM, "total_kobo": "1", "price": "1"})

    order = only_order(db)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"https://checkout.paystack.com/{order.paystack_reference}"
    assert order.status == "pending" and order.total_kobo == 1_000_000
    assert order.user_id == buyer.id
    assert paystack.initialized == [
        {
            "email": buyer.email,
            "amount": 1_000_000,
            "reference": order.paystack_reference,
            "callback_url": "http://testserver/payments/callback",
        }
    ]
    # The cart is kept until payment is confirmed.
    assert read_session(db_client)["cart"] == {str(rice.id): 2}


def test_checkout_rejects_quantity_above_current_stock(db, db_client, buyer, paystack):
    rice = make_product(db, stock=5)
    db_client.post("/cart/add", data={"product_id": rice.id, "qty": 5})
    rice.stock = 2
    db.commit()

    resp = checkout(db_client)
    assert resp.headers["location"] == "/cart"
    assert db.scalars(select(Order)).all() == []


def test_checkout_rejects_missing_shipping_details(db, db_client, buyer, paystack):
    rice = make_product(db)
    db_client.post("/cart/add", data={"product_id": rice.id, "qty": 1})
    resp = checkout(db_client, {"shipping_name": "", "shipping_phone": "abc", "shipping_address": ""})
    assert resp.status_code == 400
    assert "Enter a valid phone number" in resp.text
    assert db.scalars(select(Order)).all() == []


def test_paystack_init_failure_shows_error_and_keeps_cart(db, db_client, buyer, monkeypatch):
    async def failing_init(**kwargs):
        raise payments.PaymentError("down")

    monkeypatch.setattr(payments, "initialize_transaction", failing_init)
    rice = make_product(db)
    db_client.post("/cart/add", data={"product_id": rice.id, "qty": 1})

    resp = checkout(db_client)
    assert resp.headers["location"] == "/checkout"
    assert read_session(db_client)["cart"] == {str(rice.id): 1}


# ---- callback ------------------------------------------------------------------


def start_checkout(db, client, paystack, qty=2, stock=5):
    rice = make_product(db, price_kobo=500_000, stock=stock)
    client.post("/cart/add", data={"product_id": rice.id, "qty": qty})
    checkout(client)
    return rice, only_order(db)


def stock_of(db, product: Product) -> int:
    db.expire_all()
    return db.get(Product, product.id).stock


def test_callback_marks_paid_clears_cart_and_emails(db, db_client, buyer, paystack, emails):
    rice, order = start_checkout(db, db_client, paystack)
    paystack.paid[order.paystack_reference] = order.total_kobo

    resp = db_client.get(f"/payments/callback?trxref={order.paystack_reference}&reference={order.paystack_reference}")

    assert resp.status_code == 200 and "Payment successful" in resp.text
    assert only_order(db).status == "paid"
    assert stock_of(db, rice) == 3
    assert read_session(db_client).get("cart") in (None, {})
    assert emails == [(buyer.email, f"Order #{order.id} confirmed")]


def test_callback_with_wrong_amount_does_not_mark_paid(db, db_client, buyer, paystack, emails):
    rice, order = start_checkout(db, db_client, paystack)
    paystack.paid[order.paystack_reference] = 100  # tampered: paid ₦1

    resp = db_client.get(f"/payments/callback?reference={order.paystack_reference}")

    assert "Payment not completed" in resp.text
    assert only_order(db).status == "pending"
    assert stock_of(db, rice) == 5
    assert emails == []
    assert read_session(db_client)["cart"]  # kept so they can retry


def test_callback_for_abandoned_payment_offers_retry(db, db_client, buyer, paystack):
    _, order = start_checkout(db, db_client, paystack)
    resp = db_client.get(f"/payments/callback?reference={order.paystack_reference}")
    assert "Payment not completed" in resp.text and 'href="/checkout"' in resp.text


def test_callback_for_unknown_reference_is_404(db, db_client, buyer, paystack):
    assert db_client.get("/payments/callback?reference=ord_nope").status_code == 404
    assert db_client.get("/payments/callback").status_code == 404


def test_callback_for_someone_elses_order_is_404(db, db_client, buyer, paystack):
    _, order = start_checkout(db, db_client, paystack)
    paystack.paid[order.paystack_reference] = order.total_kobo
    other = make_user(db, "other@example.com")
    sign_in(db_client, other.email, user_id=other.id)

    assert db_client.get(f"/payments/callback?reference={order.paystack_reference}").status_code == 404
    assert only_order(db).status == "pending"


def test_callback_when_paystack_unreachable_does_not_mark_paid(db, db_client, buyer, paystack, monkeypatch):
    _, order = start_checkout(db, db_client, paystack)

    async def down(reference):
        raise payments.PaymentError("timeout")

    monkeypatch.setattr(payments, "verify_transaction", down)
    resp = db_client.get(f"/payments/callback?reference={order.paystack_reference}")
    assert resp.status_code == 200 and "couldn't confirm" in resp.text
    assert only_order(db).status == "pending"


# ---- webhook -------------------------------------------------------------------


def webhook(client, payload: dict, signature: str | None = None):
    body = json.dumps(payload).encode()
    sig = signature if signature is not None else hmac.new(b"sk_test_x", body, hashlib.sha512).hexdigest()
    return client.post(
        "/payments/webhook", content=body, headers={"x-paystack-signature": sig, "content-type": "application/json"}
    )


def charge_success(order, amount=None):
    return {
        "event": "charge.success",
        "data": {
            "status": "success",
            "reference": order.paystack_reference,
            "amount": order.total_kobo if amount is None else amount,
            "currency": "NGN",
        },
    }


def test_webhook_rejects_bad_signature(db, db_client, buyer, paystack):
    _, order = start_checkout(db, db_client, paystack)
    db_client.cookies.clear()
    assert webhook(db_client, charge_success(order), signature="forged").status_code == 401
    assert only_order(db).status == "pending"


def test_webhook_marks_order_paid_without_a_login(db, db_client, buyer, paystack, emails):
    rice, order = start_checkout(db, db_client, paystack)
    db_client.cookies.clear()  # Paystack's servers have no session

    resp = webhook(db_client, charge_success(order))

    assert resp.status_code == 200
    assert only_order(db).status == "paid"
    assert stock_of(db, rice) == 3
    assert len(emails) == 1


def test_webhook_with_wrong_amount_is_ignored(db, db_client, buyer, paystack):
    _, order = start_checkout(db, db_client, paystack)
    assert webhook(db_client, charge_success(order, amount=1)).status_code == 200
    assert only_order(db).status == "pending"


def test_webhook_ignores_other_events_and_unknown_orders(db, db_client, buyer, paystack):
    assert webhook(db_client, {"event": "transfer.success", "data": {}}).status_code == 200
    assert webhook(db_client, {"event": "charge.success", "data": {"reference": "ord_nope"}}).status_code == 200


def test_webhook_and_callback_together_pay_once(db, db_client, buyer, paystack, emails):
    rice, order = start_checkout(db, db_client, paystack)
    paystack.paid[order.paystack_reference] = order.total_kobo

    webhook(db_client, charge_success(order))
    resp = db_client.get(f"/payments/callback?reference={order.paystack_reference}")

    assert "Payment successful" in resp.text
    assert stock_of(db, rice) == 3
    assert len(emails) == 1

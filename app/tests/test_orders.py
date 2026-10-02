import threading

from fastapi import BackgroundTasks

from app.db import SessionLocal
from app.models import Order, Product
from app.services import cart
from app.services.orders import ShippingDetails, create_pending_order, mark_order_paid
from app.tests.conftest import make_product, make_user, needs_db

pytestmark = needs_db

SHIPPING = ShippingDetails(name="Ada Buyer", phone="08012345678", address="1 Marina, Lagos")


def pending_order(db, quantities: dict[Product, int]) -> Order:
    user = make_user(db)
    summary = cart.price_cart({p.id: q for p, q in quantities.items()}, {p.id: p for p in quantities})
    return create_pending_order(db, user_id=user.id, summary=summary, shipping=SHIPPING)


def stock_of(product_id: int) -> int:
    with SessionLocal() as s:
        return s.get(Product, product_id).stock


def test_create_pending_order_snapshots_items(db):
    rice = make_product(db, "Rice", price_kobo=500_000, stock=10)
    oil = make_product(db, "Oil", price_kobo=120_000, stock=4)
    order = pending_order(db, {rice: 2, oil: 1})

    assert order.status == "pending"
    assert order.total_kobo == 1_120_000
    assert order.paystack_reference.startswith("ord_") and len(order.paystack_reference) == 36
    assert order.shipping_address == "1 Marina, Lagos"
    assert [(i.product_name, i.quantity, i.unit_price_kobo) for i in order.items] == [
        ("Rice", 2, 500_000),
        ("Oil", 1, 120_000),
    ]
    # Stock is only taken when payment is confirmed.
    assert stock_of(rice.id) == 10


def test_mark_order_paid_decrements_stock_and_queues_email_once(db):
    rice = make_product(db, "Rice", stock=10)
    order = pending_order(db, {rice: 3})
    tasks = BackgroundTasks()

    assert mark_order_paid(db, order.id, tasks) is True
    assert mark_order_paid(db, order.id, tasks) is False

    db.refresh(order)
    assert order.status == "paid" and order.paid_at is not None
    assert stock_of(rice.id) == 7
    assert len(tasks.tasks) == 1
    to, subject, html = tasks.tasks[0].args
    assert to == "buyer@example.com"
    assert f"#{order.id}" in subject
    assert "Rice" in html and "1 Marina, Lagos" in html


def test_mark_order_paid_ignores_orders_that_are_not_pending(db):
    rice = make_product(db, "Rice", stock=10)
    order = pending_order(db, {rice: 1})
    order.status = "cancelled"
    db.commit()

    assert mark_order_paid(db, order.id, BackgroundTasks()) is False
    assert stock_of(rice.id) == 10


def test_oversold_order_stays_paid_and_is_flagged(db):
    rice = make_product(db, "Rice", stock=5)
    oil = make_product(db, "Oil", stock=5)
    order = pending_order(db, {rice: 2, oil: 3})
    oil.stock = 1  # someone else bought most of it in the meantime
    db.commit()

    assert mark_order_paid(db, order.id, BackgroundTasks()) is True

    db.refresh(order)
    assert order.status == "paid"
    assert "[OVERSOLD]" in order.admin_note and "Oil" in order.admin_note
    assert stock_of(rice.id) == 3
    assert stock_of(oil.id) == 1  # never goes negative


def test_concurrent_mark_order_paid_only_pays_once(db):
    rice = make_product(db, "Rice", stock=10)
    order = pending_order(db, {rice: 4})
    results = []
    barrier = threading.Barrier(4)

    def pay():
        with SessionLocal() as session:
            barrier.wait()
            results.append(mark_order_paid(session, order.id, BackgroundTasks()))

    threads = [threading.Thread(target=pay) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [False, False, False, True]
    assert stock_of(rice.id) == 6

"""Creating orders and marking them paid. mark_order_paid is the single source of truth."""

import logging
import uuid
from dataclasses import dataclass

from fastapi import BackgroundTasks
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.models import Order, OrderItem, Product
from app.services.cart import CartSummary
from app.services.mail import render_email, send_email

log = logging.getLogger(__name__)

ORDER_STATUSES = ("pending", "paid", "shipped", "delivered", "cancelled")
PAID_STATUSES = frozenset({"paid", "shipped", "delivered"})

# Spec §8: paid→shipped→delivered; pending→cancelled; paid→cancelled.
# pending→paid is not here: only a verified payment (mark_order_paid) may do that.
ALLOWED_TRANSITIONS = {
    "pending": {"cancelled"},
    "paid": {"shipped", "cancelled"},
    "shipped": {"delivered"},
}

# Email subject for each customer notification; {id} is the order number.
EMAIL_SUBJECTS = {
    "paid": "Order #{id} confirmed",
    "shipped": "Order #{id} is on its way",
    "delivered": "Order #{id} has been delivered",
    "cancelled": "Order #{id} has been cancelled",
    "paid_after_cancel": "Refund for order #{id}",
}


class StatusChangeError(ValueError):
    """The requested status change isn't allowed; the message is shown to the admin."""


def can_transition(current: str, new: str) -> bool:
    return new in ALLOWED_TRANSITIONS.get(current, set())


@dataclass(frozen=True)
class ShippingDetails:
    name: str
    phone: str
    address: str


def new_reference() -> str:
    return "ord_" + uuid.uuid4().hex


def create_pending_order(
    db: Session, *, user_id: int, summary: CartSummary, shipping: ShippingDetails
) -> Order:
    """Save a pending order priced from the database. Stock is taken only once it is paid."""
    order = Order(
        user_id=user_id,
        total_kobo=summary.total_kobo,
        status="pending",
        paystack_reference=new_reference(),
        shipping_name=shipping.name,
        shipping_phone=shipping.phone,
        shipping_address=shipping.address,
        items=[
            OrderItem(
                product_id=line.product.id,
                product_name=line.product.name,
                quantity=line.quantity,
                unit_price_kobo=line.product.price_kobo,
            )
            for line in summary.lines
        ],
    )
    db.add(order)
    db.commit()
    return order


def mark_order_paid(db: Session, order_id: int, background_tasks: BackgroundTasks) -> bool:
    """Mark a pending order paid, take its stock and queue the confirmation email.

    Idempotent and safe to call concurrently (callback and webhook): the conditional
    UPDATE row-locks the order, so only one caller sees it move from pending to paid.
    Returns True only for that caller.
    """
    try:
        paid_id = db.scalar(
            update(Order)
            .where(Order.id == order_id, Order.status == "pending")
            .values(status="paid", paid_at=func.now())
            .returning(Order.id)
            .execution_options(synchronize_session=False)
        )
        if paid_id is None:
            flagged = _flag_paid_after_cancel(db, order_id)
            db.commit()
            if flagged:
                _queue_order_email(db, order_id, background_tasks, "paid_after_cancel")
            return False

        oversold = []
        items = db.execute(
            select(OrderItem.product_id, OrderItem.product_name, OrderItem.quantity).where(
                OrderItem.order_id == order_id
            )
        ).all()
        for item in items:
            taken = db.execute(
                update(Product)
                .where(Product.id == item.product_id, Product.stock >= item.quantity)
                .values(stock=Product.stock - item.quantity, updated_at=func.now())
                .execution_options(synchronize_session=False)
            )
            if taken.rowcount == 0:
                oversold.append(f"{item.product_name} ×{item.quantity}")

        if oversold:
            # The money was taken, so the order stays paid; the admin resolves it.
            log.warning("Order %s paid but oversold: %s", order_id, ", ".join(oversold))
            db.execute(
                update(Order)
                .where(Order.id == order_id)
                .values(admin_note=func.concat_ws(" ", Order.admin_note, "[OVERSOLD] " + ", ".join(oversold)))
                .execution_options(synchronize_session=False)
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    _queue_order_email(db, order_id, background_tasks, "paid")
    return True


def _flag_paid_after_cancel(db: Session, order_id: int) -> bool:
    """A verified payment arrived for an order an admin had already cancelled.

    The order stays cancelled and its stock untouched; it is flagged so the admin refunds
    it. paid_at doubles as the "already flagged" marker, so callback + webhook flag it once.
    """
    flagged = db.scalar(
        update(Order)
        .where(Order.id == order_id, Order.status == "cancelled", Order.paid_at.is_(None))
        .values(
            paid_at=func.now(),
            admin_note=func.concat_ws(
                " ", Order.admin_note, "[PAID AFTER CANCEL] Refund this payment in Paystack."
            ),
        )
        .returning(Order.id)
        .execution_options(synchronize_session=False)
    )
    if flagged:
        log.warning("Order %s was paid after it was cancelled; refund needed", order_id)
    return flagged is not None


def change_order_status(
    db: Session, order: Order, new_status: str, background_tasks: BackgroundTasks
) -> None:
    """Move an order to `new_status` if allowed, then notify the customer.

    The UPDATE only applies if the status is still what the admin saw, so a stale form
    (or a second admin) can't overwrite a newer change. Raises StatusChangeError.
    """
    old_status = order.status
    if not can_transition(old_status, new_status):
        raise StatusChangeError(
            f"Order #{order.id} is {old_status} and can't be changed to {new_status}."
        )
    try:
        changed = db.scalar(
            update(Order)
            .where(Order.id == order.id, Order.status == old_status)
            .values(status=new_status)
            .returning(Order.id)
            .execution_options(synchronize_session=False)
        )
        if changed is None:
            db.rollback()
            raise StatusChangeError(
                f"Order #{order.id} was just updated by someone else. Check its new status."
            )
        if old_status == "paid" and new_status == "cancelled":
            _restore_stock(db, order)
        db.commit()
    except StatusChangeError:
        raise
    except Exception:
        db.rollback()
        raise

    # Cancelling an unpaid order (usually an abandoned checkout) isn't worth an email.
    if not (old_status == "pending" and new_status == "cancelled"):
        _queue_order_email(db, order.id, background_tasks, new_status)


def _restore_stock(db: Session, order: Order) -> None:
    """Return a cancelled paid order's items to stock."""
    if "[OVERSOLD]" in (order.admin_note or ""):
        # Some items were never taken from stock, so adding them all back would overcount.
        db.execute(
            update(Order)
            .where(Order.id == order.id)
            .values(
                admin_note=func.concat_ws(
                    " ", Order.admin_note,
                    "Stock was not restored automatically because the order was oversold; "
                    "restore stock manually.",
                )
            )
            .execution_options(synchronize_session=False)
        )
        return
    for item in order.items:
        db.execute(
            update(Product)
            .where(Product.id == item.product_id)
            .values(stock=Product.stock + item.quantity, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )


def _queue_order_email(db: Session, order_id: int, background_tasks: BackgroundTasks, kind: str) -> None:
    # The order change is already saved; an email problem must never turn that into an error.
    try:
        order = db.scalar(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items), selectinload(Order.user))
            .execution_options(populate_existing=True)
        )
        template = "order_confirmation.html" if kind == "paid" else "order_status.html"
        html = render_email(template, order=order, kind=kind)
        subject = EMAIL_SUBJECTS[kind].format(id=order.id)
        background_tasks.add_task(send_email, order.user.email, subject, html, to_name=order.user.name)
    except Exception:
        log.exception("Could not queue the %s email for order %s", kind, order_id)

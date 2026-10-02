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
            db.rollback()
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

    _queue_confirmation_email(db, order_id, background_tasks)
    return True


def _queue_confirmation_email(db: Session, order_id: int, background_tasks: BackgroundTasks) -> None:
    # The payment is already saved; an email problem must never turn that into an error.
    try:
        _queue_confirmation_email_or_raise(db, order_id, background_tasks)
    except Exception:
        log.exception("Could not queue the confirmation email for order %s", order_id)


def _queue_confirmation_email_or_raise(db: Session, order_id: int, background_tasks: BackgroundTasks) -> None:
    order = db.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items), selectinload(Order.user))
        .execution_options(populate_existing=True)
    )
    html = render_email("order_confirmation.html", order=order)
    background_tasks.add_task(
        send_email, order.user.email, f"Order #{order.id} confirmed", html, to_name=order.user.name
    )

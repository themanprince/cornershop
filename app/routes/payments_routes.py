import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_user
from app.db import get_db
from app.models import Order
from app.routes.shop import SHIPPING_SESSION_KEY
from app.services import payments
from app.services.cart import clear_cart
from app.services.orders import PAID_STATUSES, mark_order_paid
from app.templating import templates

log = logging.getLogger(__name__)

router = APIRouter(prefix="/payments")

def _order_by_reference(db: Session, reference: str) -> Order | None:
    if not reference:
        return None
    return db.scalar(select(Order).where(Order.paystack_reference == reference))


@router.get("/callback")
async def payment_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: dict = Depends(require_user),
):
    """Paystack sends the customer here after paying. Verify with Paystack before trusting it."""
    reference = request.query_params.get("reference") or request.query_params.get("trxref") or ""
    order = _order_by_reference(db, reference)
    if order is None or order.user_id != user["id"]:
        raise HTTPException(status_code=404)

    # A cancelled order is still checked: if it was paid anyway, it gets flagged for a refund.
    if order.status == "pending" or (order.status == "cancelled" and order.paid_at is None):
        try:
            data = await payments.verify_transaction(reference)
        except payments.PaymentError:
            return templates.TemplateResponse(
                request, "shop/payment_result.html", {"order": order, "outcome": "unconfirmed"}
            )
        if payments.is_successful_payment(data, order):
            mark_order_paid(db, order.id, background_tasks)
        db.refresh(order)

    if order.status in PAID_STATUSES:
        clear_cart(request.session)
        request.session.pop(SHIPPING_SESSION_KEY, None)
        outcome = "paid"
    elif order.status == "cancelled":
        outcome = "cancelled"
    else:
        outcome = "failed"
    return templates.TemplateResponse(
        request, "shop/payment_result.html", {"order": order, "outcome": outcome}
    )


@router.post("/webhook")
async def paystack_webhook(
    request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    """Server-to-server notice from Paystack. Authenticated by its HMAC signature, not a login."""
    body = await request.body()
    if not payments.verify_webhook_signature(body, request.headers.get("x-paystack-signature")):
        return JSONResponse({"detail": "Invalid signature"}, status_code=401)
    try:
        event = json.loads(body)
    except ValueError:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)

    if isinstance(event, dict) and event.get("event") == "charge.success":
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        order = _order_by_reference(db, str(data.get("reference") or ""))
        if order is None:
            log.warning("Paystack webhook for unknown reference %r", data.get("reference"))
        elif payments.is_successful_payment(data, order):
            mark_order_paid(db, order.id, background_tasks)
        else:
            log.warning("Paystack webhook for order %s did not match; not marking paid", order.id)
    return {"status": "ok"}

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_user
from app.config import settings
from app.db import get_db
from app.models import Order, Product
from app.services import cart, payments
from app.services.orders import ShippingDetails, create_pending_order
from app.templating import flash, templates

router = APIRouter()

SHIPPING_SESSION_KEY = "shipping"
PHONE_ALLOWED = re.compile(r"^[0-9+()\-\s]+$")


def form_int(form, key: str, default: int = 0) -> int:
    try:
        return int(form.get(key) or default)
    except (TypeError, ValueError):
        return default


def parse_shipping_form(form) -> tuple[dict, dict]:
    """Validate the checkout shipping fields. Returns (values, errors)."""
    values = {
        "shipping_name": (form.get("shipping_name") or "").strip(),
        "shipping_phone": (form.get("shipping_phone") or "").strip(),
        "shipping_address": (form.get("shipping_address") or "").strip(),
    }
    errors = {}
    if not values["shipping_name"] or len(values["shipping_name"]) > 100:
        errors["shipping_name"] = "Enter the recipient's name."
    phone = values["shipping_phone"]
    digits = sum(ch.isdigit() for ch in phone)
    if not PHONE_ALLOWED.match(phone) or not 7 <= digits <= 15:
        errors["shipping_phone"] = "Enter a valid phone number, e.g. 0801 234 5678."
    if len(values["shipping_address"]) < 5 or len(values["shipping_address"]) > 500:
        errors["shipping_address"] = "Enter the full delivery address."
    return values, errors


def _active_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None or not product.is_active:
        raise HTTPException(status_code=404)
    return product


def _back_to_cart(request: Request, summary: cart.CartSummary) -> RedirectResponse:
    for problem in summary.problems or ["Your cart is empty."]:
        flash(request, problem, "warning")
    return RedirectResponse("/cart", status_code=303)


@router.get("/shop")
def shop(request: Request, db: Session = Depends(get_db)):
    products = db.scalars(
        select(Product).where(Product.is_active).order_by(Product.created_at.desc())
    ).all()
    return templates.TemplateResponse(request, "shop/index.html", {"products": products})


@router.get("/products/{product_id}")
def product_detail(
    product_id: int, request: Request, db: Session = Depends(get_db), user: dict = Depends(require_user)
):
    product = _active_product(db, product_id)
    in_cart = cart.get_cart(db, user["id"]).get(product.id, 0)
    return templates.TemplateResponse(
        request, "shop/product.html", {"product": product, "in_cart": in_cart}
    )


@router.get("/cart")
def cart_page(request: Request, db: Session = Depends(get_db), user: dict = Depends(require_user)):
    summary = cart.load_cart(db, user["id"])
    return templates.TemplateResponse(request, "shop/cart.html", {"summary": summary})


@router.post("/cart/add")
async def cart_add(request: Request, db: Session = Depends(get_db), user: dict = Depends(require_user)):
    form = await request.form()
    product = _active_product(db, form_int(form, "product_id"))
    wanted = form_int(form, "qty", 1)
    before = cart.get_cart(db, user["id"]).get(product.id, 0)
    in_cart = cart.add_item(db, user["id"], product.id, wanted, stock=product.stock)
    if in_cart == 0:
        flash(request, f"Sorry, “{product.name}” is out of stock.", "warning")
    elif in_cart - before < wanted:
        flash(request, f"Only {product.stock} of “{product.name}” available; your cart has {in_cart}.", "info")
    else:
        flash(request, f"Added “{product.name}” to your cart.", "success")
    return RedirectResponse("/cart", status_code=303)


@router.post("/cart/update")
async def cart_update(request: Request, db: Session = Depends(get_db), user: dict = Depends(require_user)):
    form = await request.form()
    product_id = form_int(form, "product_id")
    product = db.get(Product, product_id)
    stock = product.stock if product is not None and product.is_active else 0
    cart.set_quantity(db, user["id"], product_id, form_int(form, "qty"), stock=stock)
    return RedirectResponse("/cart", status_code=303)


def _render_checkout(request: Request, summary, values: dict, errors: dict, status: int = 200):
    return templates.TemplateResponse(
        request,
        "shop/checkout.html",
        {"summary": summary, "values": values, "errors": errors},
        status_code=status,
    )


@router.get("/checkout")
def checkout_page(request: Request, db: Session = Depends(get_db), user: dict = Depends(require_user)):
    summary = cart.load_cart(db, user["id"])
    if not summary.ok:
        return _back_to_cart(request, summary)
    values = request.session.get(SHIPPING_SESSION_KEY) or {
        "shipping_name": user["name"],
        "shipping_phone": "",
        "shipping_address": "",
    }
    return _render_checkout(request, summary, values, {})


@router.post("/checkout")
async def checkout_submit(
    request: Request, db: Session = Depends(get_db), user: dict = Depends(require_user)
):
    # Prices and stock always come from the database, never from the form.
    summary = cart.load_cart(db, user["id"])
    if not summary.ok:
        return _back_to_cart(request, summary)

    values, errors = parse_shipping_form(await request.form())
    if errors:
        return _render_checkout(request, summary, values, errors, status=400)
    request.session[SHIPPING_SESSION_KEY] = values  # prefills the form if they retry

    order = create_pending_order(
        db,
        user_id=user["id"],
        summary=summary,
        shipping=ShippingDetails(
            name=values["shipping_name"],
            phone=values["shipping_phone"],
            address=values["shipping_address"],
        ),
    )
    try:
        checkout_url = await payments.initialize_transaction(
            email=user["email"],
            amount_kobo=order.total_kobo,
            reference=order.paystack_reference,
            callback_url=f"{settings.base_url}/payments/callback",
        )
    except payments.PaymentError:
        # The order stays pending: if a charge somehow went through, the webhook can still pay it.
        flash(request, "We couldn't start the payment. Please try again in a moment.", "danger")
        return RedirectResponse("/checkout", status_code=303)

    # The cart is cleared only once the payment is verified.
    return RedirectResponse(checkout_url, status_code=303)


@router.get("/orders")
def my_orders(request: Request, db: Session = Depends(get_db), user: dict = Depends(require_user)):
    orders = db.scalars(
        select(Order)
        .where(Order.user_id == user["id"])
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
    ).all()
    return templates.TemplateResponse(request, "shop/orders.html", {"orders": orders})


@router.get("/orders/{order_id}")
def my_order_detail(
    order_id: int, request: Request, db: Session = Depends(get_db), user: dict = Depends(require_user)
):
    order = db.get(Order, order_id)
    # 404 rather than 403, so order numbers of other customers aren't confirmed to exist.
    if order is None or order.user_id != user["id"]:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "shop/order_detail.html", {"order": order})

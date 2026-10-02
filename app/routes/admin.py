import logging
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import UploadFile

from app.auth import require_admin
from app.db import get_db
from app.models import Order, Product
from app.services.orders import (
    ALLOWED_TRANSITIONS,
    ORDER_STATUSES,
    PAID_STATUSES,
    StatusChangeError,
    change_order_status,
)
from app.services.storage import UploadError, delete_image, upload_image
from app.templating import flash, templates

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

INT4_MAX = 2_147_483_647
LOW_STOCK_THRESHOLD = 3
ORDERS_PAGE_LIMIT = 200  # TODO(spec): pagination is out of scope for the MVP.


def parse_naira(raw: str) -> int:
    """'2,500.50' -> 250050 kobo. Raises ValueError with a user-facing message."""
    try:
        amount = Decimal(raw.replace(",", "").replace("₦", "").strip())
    except InvalidOperation:
        raise ValueError("Enter a price like 2500 or 2500.50.") from None
    if not amount.is_finite() or amount < 0:
        raise ValueError("Price must be zero or more.")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("Price can have at most 2 decimal places.")
    kobo = int(amount * 100)
    if kobo > INT4_MAX:
        raise ValueError("Price is too large.")
    return kobo


def parse_product_form(form) -> tuple[dict, dict]:
    """Validate the text fields of the product form. Returns (values, errors)."""
    values = {
        "name": (form.get("name") or "").strip(),
        "description": (form.get("description") or "").strip(),
        "price": (form.get("price") or "").strip(),
        "stock": (form.get("stock") or "").strip(),
        "is_active": form.get("is_active") is not None,
    }
    errors = {}
    if not values["name"]:
        errors["name"] = "Name is required."
    try:
        values["price_kobo"] = parse_naira(values["price"])
    except ValueError as exc:
        errors["price"] = str(exc)
    try:
        values["stock_int"] = int(values["stock"] or "0")
        if not 0 <= values["stock_int"] <= INT4_MAX:
            raise ValueError
    except ValueError:
        errors["stock"] = "Stock must be a whole number, zero or more."
    return values, errors


def _chosen_file(form) -> UploadFile | None:
    image = form.get("image")
    # Browsers send an empty file part when nothing was chosen.
    if isinstance(image, UploadFile) and image.filename:
        return image
    return None


def _render_form(request: Request, product: Product | None, values: dict, errors: dict, status=200):
    return templates.TemplateResponse(
        request,
        "admin/product_form.html",
        {"product": product, "values": values, "errors": errors},
        status_code=status,
    )


def _get_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404)
    return product


@router.get("")
def overview(request: Request, db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Order.status, func.count()).group_by(Order.status)).all())
    revenue_kobo = db.scalar(
        select(func.coalesce(func.sum(Order.total_kobo), 0)).where(Order.status.in_(PAID_STATUSES))
    )
    low_stock = db.scalars(
        select(Product)
        .where(Product.is_active, Product.stock <= LOW_STOCK_THRESHOLD)
        .order_by(Product.stock, Product.name)
        .limit(10)
    ).all()
    flagged = db.scalars(
        select(Order).where(Order.admin_note.is_not(None)).order_by(Order.created_at.desc()).limit(5)
    ).all()
    recent = db.scalars(
        select(Order).options(selectinload(Order.user)).order_by(Order.created_at.desc()).limit(5)
    ).all()
    return templates.TemplateResponse(
        request,
        "admin/overview.html",
        {
            "counts": counts,
            "revenue_kobo": revenue_kobo,
            "low_stock": low_stock,
            "low_stock_threshold": LOW_STOCK_THRESHOLD,
            "flagged": flagged,
            "recent": recent,
        },
    )


@router.get("/orders")
def order_list(request: Request, status: str = "", db: Session = Depends(get_db)):
    status = status if status in ORDER_STATUSES else ""
    query = select(Order).options(selectinload(Order.user)).order_by(Order.created_at.desc())
    if status:
        query = query.where(Order.status == status)
    orders = db.scalars(query.limit(ORDERS_PAGE_LIMIT)).all()
    return templates.TemplateResponse(
        request,
        "admin/orders.html",
        {"orders": orders, "status": status, "statuses": ORDER_STATUSES},
    )


def _get_order(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404)
    return order


@router.get("/orders/{order_id}")
def order_detail(order_id: int, request: Request, db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    # Forward steps first, cancel last.
    next_statuses = sorted(ALLOWED_TRANSITIONS.get(order.status, ()), key=ORDER_STATUSES.index)
    return templates.TemplateResponse(
        request, "admin/order_detail.html", {"order": order, "next_statuses": next_statuses}
    )


@router.post("/orders/{order_id}/status")
async def order_set_status(
    order_id: int, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    order = _get_order(db, order_id)
    new_status = str((await request.form()).get("status") or "")
    try:
        change_order_status(db, order, new_status, background_tasks)
    except StatusChangeError as exc:
        flash(request, str(exc), "danger")
    else:
        flash(request, f"Order #{order_id} is now {new_status}.", "success")
    return RedirectResponse(f"/admin/orders/{order_id}", status_code=303)


@router.get("/products")
def product_list(request: Request, db: Session = Depends(get_db)):
    products = db.scalars(select(Product).order_by(Product.created_at.desc())).all()
    return templates.TemplateResponse(request, "admin/products.html", {"products": products})


@router.get("/products/new")
def product_new(request: Request):
    values = {"name": "", "description": "", "price": "", "stock": "0", "is_active": True}
    return _render_form(request, None, values, {})


# The upload routes are async because storage uploads are async; their DB calls are
# short sync calls on the event loop, which is acceptable at MVP traffic.
@router.post("/products/new")
async def product_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    values, errors = parse_product_form(form)
    image = _chosen_file(form)
    image_url = None
    if not errors and image:
        try:
            image_url = await upload_image(image)
        except UploadError as exc:
            errors["image"] = str(exc)
    if errors:
        return _render_form(request, None, values, errors, status=400)

    product = Product(
        name=values["name"],
        description=values["description"],
        price_kobo=values["price_kobo"],
        stock=values["stock_int"],
        is_active=values["is_active"],
        image_url=image_url,
    )
    db.add(product)
    try:
        db.commit()
    except Exception:
        db.rollback()
        await delete_image(image_url)
        raise
    flash(request, f"Created “{product.name}”.", "success")
    return RedirectResponse("/admin/products", status_code=303)


@router.get("/products/{product_id}/edit")
def product_edit(product_id: int, request: Request, db: Session = Depends(get_db)):
    product = _get_product(db, product_id)
    values = {
        "name": product.name,
        "description": product.description,
        "price": f"{product.price_kobo / 100:.2f}",
        "stock": str(product.stock),
        "is_active": product.is_active,
    }
    return _render_form(request, product, values, {})


@router.post("/products/{product_id}/edit")
async def product_update(product_id: int, request: Request, db: Session = Depends(get_db)):
    product = _get_product(db, product_id)
    form = await request.form()
    values, errors = parse_product_form(form)
    image = _chosen_file(form)
    new_url = None
    if not errors and image:
        try:
            new_url = await upload_image(image)
        except UploadError as exc:
            errors["image"] = str(exc)
    if errors:
        return _render_form(request, product, values, errors, status=400)

    # Spec §9.2.5: upload new image, update the DB, then best-effort delete the old one.
    old_url = product.image_url
    product.name = values["name"]
    product.description = values["description"]
    product.price_kobo = values["price_kobo"]
    product.stock = values["stock_int"]
    product.is_active = values["is_active"]
    if new_url:
        product.image_url = new_url
    try:
        db.commit()
    except Exception:
        db.rollback()
        await delete_image(new_url)
        raise
    if new_url:
        await delete_image(old_url)
    flash(request, f"Saved “{product.name}”.", "success")
    return RedirectResponse("/admin/products", status_code=303)


@router.post("/products/{product_id}/delete")
def product_delete(product_id: int, request: Request, db: Session = Depends(get_db)):
    # Soft delete only: orders keep referencing the product (spec §7).
    # TODO(spec): hard delete when no order_items reference it (on the cut list).
    product = _get_product(db, product_id)
    product.is_active = False
    db.commit()
    flash(request, f"Deactivated “{product.name}”. Edit it to reactivate.", "info")
    return RedirectResponse("/admin/products", status_code=303)

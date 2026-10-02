from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Product
from app.templating import templates

router = APIRouter()


@router.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    products = db.scalars(
        select(Product).where(Product.is_active).order_by(Product.created_at.desc())
    ).all()
    return templates.TemplateResponse(request, "shop/index.html", {"products": products})


@router.get("/products/{product_id}")
def product_detail(product_id: int, request: Request, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None or not product.is_active:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "shop/product.html", {"product": product})

"""Shopping cart, stored per account in the database so it follows the shopper across devices.

Only product ids and quantities are stored; prices always come from the products table.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import CartItem, Product


def get_cart(db: Session, user_id: int) -> dict[int, int]:
    """Return {product_id: quantity} for the user's cart."""
    items = db.scalars(select(CartItem).where(CartItem.user_id == user_id)).all()
    return {item.product_id: item.quantity for item in items}


def set_quantity(db: Session, user_id: int, product_id: int, quantity: int, stock: int) -> int:
    """Set an item's quantity, capped at stock. 0 removes it. Returns the stored quantity."""
    quantity = max(0, min(quantity, stock))
    if quantity:
        # Upsert, so two devices adding the same product at once can't create duplicate rows.
        db.execute(
            insert(CartItem)
            .values(user_id=user_id, product_id=product_id, quantity=quantity)
            .on_conflict_do_update(
                index_elements=[CartItem.user_id, CartItem.product_id],
                set_={"quantity": quantity, "updated_at": func.now()},
            )
        )
    else:
        db.execute(
            delete(CartItem).where(CartItem.user_id == user_id, CartItem.product_id == product_id)
        )
    db.commit()
    return quantity


def add_item(db: Session, user_id: int, product_id: int, quantity: int, stock: int) -> int:
    current = get_cart(db, user_id).get(product_id, 0)
    return set_quantity(db, user_id, product_id, current + max(quantity, 0), stock)


def clear_cart(db: Session, user_id: int) -> None:
    db.execute(delete(CartItem).where(CartItem.user_id == user_id))
    db.commit()


def item_count(db: Session, user_id: int) -> int:
    total = db.scalar(select(func.sum(CartItem.quantity)).where(CartItem.user_id == user_id))
    return total or 0


@dataclass
class CartLine:
    product: Product
    quantity: int

    @property
    def line_total_kobo(self) -> int:
        return self.product.price_kobo * self.quantity


@dataclass
class CartSummary:
    lines: list[CartLine] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def total_kobo(self) -> int:
        return sum(line.line_total_kobo for line in self.lines)

    @property
    def ok(self) -> bool:
        """True when the cart can be checked out as-is."""
        return bool(self.lines) and not self.problems


def price_cart(cart: Mapping[int, int], products: Mapping[int, Product]) -> CartSummary:
    """Price the cart from database products and list anything that blocks checkout."""
    summary = CartSummary()
    for product_id, quantity in cart.items():
        product = products.get(product_id)
        if product is None or not product.is_active:
            summary.problems.append("An item in your cart is no longer available and was left out.")
            continue
        if product.stock < quantity:
            if product.stock == 0:
                summary.problems.append(f"“{product.name}” is out of stock. Please remove it.")
            else:
                summary.problems.append(f"Only {product.stock} of “{product.name}” left. Please lower the quantity.")
        summary.lines.append(CartLine(product, quantity))
    return summary


def load_cart(db: Session, user_id: int) -> CartSummary:
    cart = get_cart(db, user_id)
    if not cart:
        return CartSummary()
    products = db.scalars(select(Product).where(Product.id.in_(cart))).all()
    return price_cart(cart, {p.id: p for p in products})

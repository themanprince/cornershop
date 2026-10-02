"""Session cart. The cookie holds only product ids and quantities, never prices."""

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product

CART_KEY = "cart"


def get_cart(session: Mapping) -> dict[int, int]:
    """Return {product_id: quantity}, dropping anything malformed or non-positive."""
    cart = {}
    for raw_id, raw_qty in (session.get(CART_KEY) or {}).items():
        try:
            product_id, quantity = int(raw_id), int(raw_qty)
        except (TypeError, ValueError):
            continue
        if quantity > 0:
            cart[product_id] = quantity
    return cart


def _save(session: MutableMapping, cart: dict[int, int]) -> None:
    session[CART_KEY] = {str(product_id): qty for product_id, qty in cart.items() if qty > 0}


def set_quantity(session: MutableMapping, product_id: int, quantity: int, stock: int) -> int:
    """Set an item's quantity, capped at stock. 0 removes it. Returns the stored quantity."""
    cart = get_cart(session)
    quantity = max(0, min(quantity, stock))
    if quantity:
        cart[product_id] = quantity
    else:
        cart.pop(product_id, None)
    _save(session, cart)
    return quantity


def add_item(session: MutableMapping, product_id: int, quantity: int, stock: int) -> int:
    current = get_cart(session).get(product_id, 0)
    return set_quantity(session, product_id, current + max(quantity, 0), stock)


def clear_cart(session: MutableMapping) -> None:
    session.pop(CART_KEY, None)


def item_count(session: Mapping) -> int:
    return sum(get_cart(session).values())


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


def load_cart(db: Session, session: Mapping) -> CartSummary:
    cart = get_cart(session)
    if not cart:
        return CartSummary()
    products = db.scalars(select(Product).where(Product.id.in_(cart))).all()
    return price_cart(cart, {p.id: p for p in products})

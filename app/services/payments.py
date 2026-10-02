"""Paystack API calls. Every payment is verified server-side before an order is paid."""

import hashlib
import hmac
import logging
from urllib.parse import quote

import httpx

from app.config import settings

log = logging.getLogger(__name__)

PAYSTACK_API = "https://api.paystack.co"


class PaymentError(Exception):
    """Paystack could not be reached or refused the request."""


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.paystack_secret_key}"}


def paystack_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=PAYSTACK_API, headers=auth_headers(), timeout=15)


async def _call(method: str, path: str, **kwargs) -> dict:
    """Make a Paystack API call and return its `data`, or raise PaymentError."""
    try:
        async with paystack_client() as client:
            resp = await client.request(method, path, **kwargs)
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.error("Paystack %s %s failed: %s", method, path, exc)
        raise PaymentError("Could not reach the payment provider.") from exc
    if resp.status_code >= 300 or not body.get("status"):
        log.error("Paystack %s %s refused: %s %s", method, path, resp.status_code, body.get("message"))
        raise PaymentError(body.get("message") or "The payment provider refused the request.")
    return body.get("data") or {}


async def initialize_transaction(*, email: str, amount_kobo: int, reference: str, callback_url: str) -> str:
    """Start a payment and return the Paystack checkout URL to send the customer to."""
    data = await _call(
        "POST",
        "/transaction/initialize",
        json={
            "email": email,
            "amount": amount_kobo,
            "reference": reference,
            "currency": "NGN",
            "callback_url": callback_url,
        },
    )
    if not data.get("authorization_url"):
        raise PaymentError("The payment provider did not return a checkout link.")
    return data["authorization_url"]


async def verify_transaction(reference: str) -> dict:
    return await _call("GET", f"/transaction/verify/{quote(reference, safe='')}")


def verify_webhook_signature(raw_body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = hmac.new(settings.paystack_secret_key.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def is_successful_payment(data: dict, order) -> bool:
    """True only if Paystack reports full payment, in naira, for this exact order."""
    amount = data.get("amount")
    return (
        data.get("status") == "success"
        and data.get("reference") == order.paystack_reference
        and data.get("currency") == "NGN"
        and type(amount) is int
        and amount == order.total_kobo
    )

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import payments

ORDER = SimpleNamespace(paystack_reference="ord_abc", total_kobo=250000)


def good_data(**overrides):
    data = {"status": "success", "reference": "ord_abc", "amount": 250000, "currency": "NGN"}
    return {**data, **overrides}


def test_successful_payment_matches_order():
    assert payments.is_successful_payment(good_data(), ORDER)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "failed"},
        {"status": "abandoned"},
        {"amount": 100},
        {"amount": "250000"},
        {"currency": "USD"},
        {"reference": "ord_other"},
    ],
)
def test_payment_mismatches_are_rejected(overrides):
    assert not payments.is_successful_payment(good_data(**overrides), ORDER)


def sign(body: bytes) -> str:
    return hmac.new(b"sk_test_x", body, hashlib.sha512).hexdigest()


def test_webhook_signature():
    body = b'{"event":"charge.success"}'
    assert payments.verify_webhook_signature(body, sign(body))
    assert not payments.verify_webhook_signature(body, sign(body + b" "))
    assert not payments.verify_webhook_signature(body, None)
    assert not payments.verify_webhook_signature(body, "")


def mock_paystack(monkeypatch, handler):
    calls = []

    def recording_handler(request):
        calls.append(request)
        return handler(request)

    def client():
        return httpx.AsyncClient(
            base_url=payments.PAYSTACK_API,
            headers=payments.auth_headers(),
            transport=httpx.MockTransport(recording_handler),
        )

    monkeypatch.setattr(payments, "paystack_client", client)
    return calls


def test_initialize_transaction_returns_authorization_url(monkeypatch):
    calls = mock_paystack(
        monkeypatch,
        lambda r: httpx.Response(
            200, json={"status": True, "data": {"authorization_url": "https://checkout.paystack.com/x"}}
        ),
    )
    url = asyncio.run(
        payments.initialize_transaction(
            email="a@b.co", amount_kobo=250000, reference="ord_abc", callback_url="http://t/cb"
        )
    )
    assert url == "https://checkout.paystack.com/x"
    request = calls[0]
    assert request.url.path == "/transaction/initialize"
    assert request.headers["authorization"] == "Bearer sk_test_x"
    assert json.loads(request.content) == {
        "email": "a@b.co",
        "amount": 250000,
        "reference": "ord_abc",
        "currency": "NGN",
        "callback_url": "http://t/cb",
    }


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"status": False, "message": "Invalid key"}),
        httpx.Response(200, json={"status": False, "message": "nope"}),
        httpx.Response(200, text="not json"),
    ],
)
def test_initialize_transaction_errors(monkeypatch, response):
    mock_paystack(monkeypatch, lambda r: response)
    with pytest.raises(payments.PaymentError):
        asyncio.run(
            payments.initialize_transaction(
                email="a@b.co", amount_kobo=1, reference="ord_abc", callback_url="http://t/cb"
            )
        )


def test_network_failure_is_a_payment_error(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("down")

    mock_paystack(monkeypatch, boom)
    with pytest.raises(payments.PaymentError):
        asyncio.run(payments.verify_transaction("ord_abc"))


def test_verify_transaction_returns_data(monkeypatch):
    calls = mock_paystack(
        monkeypatch, lambda r: httpx.Response(200, json={"status": True, "data": good_data()})
    )
    assert asyncio.run(payments.verify_transaction("ord_abc")) == good_data()
    assert calls[0].url.path == "/transaction/verify/ord_abc"

import asyncio
import io

import httpx
import pytest
from starlette.datastructures import Headers, UploadFile

from app.routes.admin import parse_naira, parse_product_form
from app.services import storage
from app.tests.conftest import sign_in


@pytest.mark.parametrize(
    "raw,kobo",
    [("2500", 250000), ("2,500.50", 250050), ("₦10", 1000), ("0", 0), ("0.1", 10), ("19.99", 1999)],
)
def test_parse_naira(raw, kobo):
    assert parse_naira(raw) == kobo


@pytest.mark.parametrize("raw", ["", "abc", "-1", "1.234", "NaN", "Infinity", "99999999999"])
def test_parse_naira_rejects(raw):
    with pytest.raises(ValueError):
        parse_naira(raw)


def test_product_form_errors():
    _, errors = parse_product_form({"name": " ", "price": "x", "stock": "-2"})
    assert set(errors) == {"name", "price", "stock"}


def test_product_form_ok():
    values, errors = parse_product_form(
        {"name": "Rice", "price": "1500.5", "stock": "3", "is_active": "on"}
    )
    assert errors == {}
    assert values["price_kobo"] == 150050 and values["stock_int"] == 3 and values["is_active"]


def test_invalid_form_rerenders_with_errors(client):
    sign_in(client, "admin@example.com")
    resp = client.post("/admin/products/new", data={"name": "", "price": "abc", "stock": "1"})
    assert resp.status_code == 400
    assert "Name is required." in resp.text


def test_missing_product_is_404(client):
    sign_in(client, "shopper@example.com")
    assert client.get("/products/999").status_code == 404


def _upload(data: bytes, content_type: str) -> UploadFile:
    return UploadFile(io.BytesIO(data), filename="f", headers=Headers({"content-type": content_type}))



@pytest.mark.parametrize(
    "data,ctype,message",
    [
        (b"x", "application/pdf", "Only image or video"),
        (b"", "image/png", "empty"),
        (b"x" * (storage.MAX_BYTES + 1), "image/png", "5 MB"),
    ],
)
def test_upload_rejects_bad_files(data, ctype, message):
    with pytest.raises(storage.UploadError, match=message):
        asyncio.run(storage.upload_image(_upload(data, ctype)))


def test_upload_posts_to_supabase(monkeypatch):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(200, json={"Key": "ok"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        storage.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    url = asyncio.run(storage.upload_image(_upload(b"png-bytes", "image/png")))
    assert url.startswith("https://example.supabase.co/storage/v1/object/public/bucket/")
    assert url.endswith(".png")
    assert calls[0].headers["content-type"] == "image/png"
    assert calls[0].headers["apikey"] == "sb_secret_test"

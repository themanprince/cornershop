import asyncio
import base64
import io
import json

import httpx
import pytest
from starlette.datastructures import Headers, UploadFile

from app.services import storage


def jwt_with(payload: dict) -> str:
    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64(payload)}.signature"


# ---- spotting the wrong kind of key ------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "sb_publishable_abc123",
        jwt_with({"role": "anon", "iss": "supabase"}),
        jwt_with({"role": "authenticated"}),
    ],
)
def test_public_keys_are_reported(key):
    problem = storage.key_problem(key)
    assert problem and "SUPABASE_SERVICE_ROLE_KEY" in problem


@pytest.mark.parametrize(
    "key", ["sb_secret_abc123", jwt_with({"role": "service_role"}), "eyJnot-a-real-jwt", "something-else"]
)
def test_secret_keys_and_unknown_formats_pass(key):
    assert storage.key_problem(key) is None


# ---- explaining Supabase's refusals ---------------------------------------------
# Supabase Storage often answers HTTP 400 with the real status inside the JSON body.


def explain(status, body, key="sb_secret_x"):
    text = json.dumps(body) if isinstance(body, dict) else body
    return storage.explain_failure(status, text, key)


def test_missing_bucket_names_the_setting():
    msg = explain(400, {"statusCode": "404", "error": "Bucket not found", "message": "Bucket not found"})
    assert "SUPABASE_BUCKET" in msg and "case-sensitive" in msg and "“bucket”" in msg


@pytest.mark.parametrize(
    "body",
    [
        {"statusCode": "403", "error": "Unauthorized", "message": "new row violates row-level security policy"},
        {"statusCode": "400", "error": "Invalid JWT", "message": "invalid signature"},
        {"message": "Invalid API key"},
    ],
)
def test_rejected_key_names_the_setting(body):
    assert "SUPABASE_SERVICE_ROLE_KEY" in explain(400, body)


def test_rejected_upload_with_a_public_key_says_so():
    msg = explain(400, {"message": "new row violates row-level security policy"}, key="sb_publishable_x")
    assert "publishable" in msg


def test_bucket_size_limit():
    msg = explain(413, {"statusCode": "413", "error": "Payload too large", "message": "The object exceeded the maximum allowed size"})
    assert "size limit" in msg


def test_bucket_mime_restriction():
    msg = explain(400, {"statusCode": "415", "error": "invalid_mime_type", "message": "mime type image/webp is not supported"})
    assert "Allowed MIME types" in msg


def test_wrong_supabase_url():
    assert "SUPABASE_URL" in explain(404, {"message": "requested path is invalid"})


def test_unknown_errors_include_supabase_message():
    msg = explain(500, "upstream exploded")
    assert "HTTP 500" in msg and "upstream exploded" in msg


# ---- upload_image end to end ----------------------------------------------------


def png() -> UploadFile:
    return UploadFile(io.BytesIO(b"png"), filename="a.png", headers=Headers({"content-type": "image/png"}))


def mock_supabase(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        storage.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )


def test_upload_error_carries_the_explanation(monkeypatch):
    mock_supabase(monkeypatch, lambda r: httpx.Response(400, json={"statusCode": "404", "message": "Bucket not found"}))
    with pytest.raises(storage.UploadError, match="SUPABASE_BUCKET"):
        asyncio.run(storage.upload_image(png()))


def test_unreachable_supabase_is_an_upload_error_not_a_crash(monkeypatch):
    def down(request):
        raise httpx.ConnectError("name resolution failed")

    mock_supabase(monkeypatch, down)
    with pytest.raises(storage.UploadError, match="SUPABASE_URL"):
        asyncio.run(storage.upload_image(png()))

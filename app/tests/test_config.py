import pytest

from app.config import _normalize_supabase_url


@pytest.mark.parametrize(
    "raw",
    [
        "https://abc.supabase.co",
        "https://abc.supabase.co/",
        "https://abc.supabase.co/rest/v1",
        "https://abc.supabase.co/rest/v1/",
        "https://abc.supabase.co/storage/v1",
        "https://abc.supabase.co/auth/v1/",
        "  https://abc.supabase.co/rest/v1  ",
    ],
)
def test_supabase_url_is_reduced_to_the_project_url(raw):
    assert _normalize_supabase_url(raw) == "https://abc.supabase.co"

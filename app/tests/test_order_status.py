import pytest

from app.services.orders import ALLOWED_TRANSITIONS, can_transition


@pytest.mark.parametrize(
    "current,new",
    [("paid", "shipped"), ("shipped", "delivered"), ("pending", "cancelled"), ("paid", "cancelled")],
)
def test_allowed_transitions(current, new):
    assert can_transition(current, new)


@pytest.mark.parametrize(
    "current,new",
    [
        ("pending", "paid"),  # only a verified payment may do this
        ("pending", "shipped"),
        ("paid", "delivered"),
        ("shipped", "cancelled"),
        ("delivered", "shipped"),
        ("cancelled", "paid"),
        ("paid", "paid"),
        ("paid", "bogus"),
    ],
)
def test_disallowed_transitions(current, new):
    assert not can_transition(current, new)


def test_terminal_statuses_have_no_next_step():
    assert "delivered" not in ALLOWED_TRANSITIONS
    assert "cancelled" not in ALLOWED_TRANSITIONS

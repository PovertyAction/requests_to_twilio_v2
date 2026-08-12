"""Tests for PII-safe logging.

The pre-2.0 launcher printed every respondent's full payload to stdout. These
tests pin down that phone numbers cannot reach a log handler intact, even when a
caller interpolates one directly into a message.
"""

import logging

import pytest

from requests_to_twilio.log import PhoneRedactingFilter, configure, mask_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+155555501234", "+********8169"),
        ("whatsapp:+155555501234", "whatsapp:+********8169"),
        ("+1 773 322 0947", "+*******0947"),
        ("+57 300 123 4567", "+********4567"),
        ("1234", "****"),
        ("12", "**"),
    ],
)
def test_mask_phone_keeps_only_last_four(raw, expected):
    assert mask_phone(raw) == expected


def test_mask_phone_preserves_channel_prefix():
    assert mask_phone("whatsapp:+15555550100").startswith("whatsapp:")


def test_masked_number_contains_no_leading_digits():
    masked = mask_phone("+155555501234")
    assert "320619" not in masked
    assert masked.endswith("8169")


class TestRedactingFilter:
    def make_record(self, message: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="rtt",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=message,
            args=(),
            exc_info=None,
        )

    def test_redacts_a_bare_number(self):
        record = self.make_record("sending to +155555501234 now")
        PhoneRedactingFilter().filter(record)
        assert "+155555501234" not in record.getMessage()
        assert "8169" in record.getMessage()

    def test_redacts_inside_a_payload_dump(self):
        """Even a careless full-payload print must not leak the number."""
        record = self.make_record("{'To': 'whatsapp:+15555550100', 'name': 'Felipe'}")
        PhoneRedactingFilter().filter(record)
        assert "+15555550100" not in record.getMessage()

    def test_leaves_ordinary_text_alone(self):
        record = self.make_record("Batch finished, sleeping for 5s")
        PhoneRedactingFilter().filter(record)
        assert record.getMessage() == "Batch finished, sleeping for 5s"

    def test_always_returns_true_so_records_still_emit(self):
        assert PhoneRedactingFilter().filter(self.make_record("+15555550100")) is True


def test_configure_attaches_the_filter_even_when_verbose():
    """There is deliberately no flag that turns redaction off."""
    logger = configure(verbose=True)
    assert logger.level == logging.DEBUG
    assert any(
        isinstance(f, PhoneRedactingFilter)
        for handler in logger.handlers
        for f in handler.filters
    )


def test_configure_is_idempotent():
    configure()
    first = len(configure().handlers)
    assert len(configure().handlers) == first == 1

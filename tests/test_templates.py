"""Tests for the WhatsApp template guards.

Submission is the irreversible step - Meta has no edit operation - so the guards
around it are worth more than the convenience they cost. The one tested here
stops a submission that can never resolve: WhatsApp does not review list pickers
at all, and a request to review one does not come back rejected, it comes back
never, which is indistinguishable from a slow approval right when a round is
waiting on it.
"""

from types import SimpleNamespace

import pytest
from twilio.base.exceptions import TwilioRestException

from requests_to_twilio.templates import (
    TemplateError,
    submit,
    unsubmittable_types,
)


def fake_client(types=None, *, fails=False):
    """Build a client whose only job is to answer one content fetch."""

    def fetch():
        if fails:
            raise TwilioRestException(
                status=404, uri="/Content", msg="gone", code=20404
            )
        return SimpleNamespace(types=types or {})

    return SimpleNamespace(
        content=SimpleNamespace(
            v1=SimpleNamespace(contents=lambda sid: SimpleNamespace(fetch=fetch))
        )
    )


class TestUnsubmittableTypes:
    def test_a_plain_template_is_submittable(self):
        client = fake_client({"twilio/text": {}})
        assert unsubmittable_types(client, "HXtext") == []

    def test_quick_reply_is_submittable(self):
        """Buttons do need approval to go out business-initiated."""
        client = fake_client({"twilio/quick-reply": {}})
        assert unsubmittable_types(client, "HXqr") == []

    def test_a_list_picker_is_not(self):
        client = fake_client({"twilio/text": {}, "twilio/list-picker": {}})
        blocked = unsubmittable_types(client, "HXlist")
        assert [name for name, _ in blocked] == ["twilio/list-picker"]

    def test_the_reason_says_what_to_do_instead(self):
        client = fake_client({"twilio/list-picker": {}})
        _, why = unsubmittable_types(client, "HXlist")[0]
        assert "24-hour" in why

    def test_an_unreachable_template_does_not_block(self):
        """A guard must never be the reason a real submission cannot go."""
        assert unsubmittable_types(fake_client(fails=True), "HXgone") == []


class TestSubmit:
    def test_refuses_a_list_picker(self):
        client = fake_client({"twilio/list-picker": {}})
        with pytest.raises(TemplateError, match="does not review"):
            submit(client, "HXlist", "demo_arm2_p1_en", "UTILITY")

    def test_rejects_a_bad_category_before_touching_the_api(self):
        with pytest.raises(TemplateError, match="category must be one of"):
            submit(fake_client(fails=True), "HXtext", "demo_intro", "PROMOTIONAL")

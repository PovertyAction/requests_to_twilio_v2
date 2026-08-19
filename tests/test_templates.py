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
    delete,
    drifted_types,
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


class TestDriftedTypes:
    """Drift between a stored template and the file that describes it.

    This is the one failure in the pipeline that no other check can see. The
    flow references a template by SID, so wording that changed in the repo but
    not on the account leaves the flow correct, `flow-check` passing, the linter
    clean and the tests green - while the respondent is read the old text.

    It has happened. A sixth question renumbered every body to "of 6"; four
    templates already existed, `create` refused to overwrite them, and a live
    respondent was asked six questions numbered out of five.
    """

    def live(self, types):
        return SimpleNamespace(types=types)

    def test_identical_content_has_not_drifted(self):
        types = {"twilio/text": {"body": "Question 1 of 6"}}
        assert drifted_types(self.live(types), {"types": types}) == []

    def test_a_reworded_body_is_reported(self):
        assert drifted_types(
            self.live({"twilio/text": {"body": "Question 1 of 5"}}),
            {"types": {"twilio/text": {"body": "Question 1 of 6"}}},
        ) == ["twilio/text"]

    def test_whitespace_alone_is_not_drift(self):
        """Twilio round-trips a trailing newline; that is not a wording change."""
        assert (
            drifted_types(
                self.live({"twilio/text": {"body": "Question 1 of 6\n"}}),
                {"types": {"twilio/text": {"body": "Question 1 of 6"}}},
            )
            == []
        )

    def test_a_changed_list_item_is_reported(self):
        """The slot labels are the answer values, so a relabelled row is drift."""
        live = {
            "twilio/list-picker": {
                "body": "When do you leave?",
                "items": [{"id": "p6_a", "item": "Sat 09:45", "description": "Sat"}],
            }
        }
        local = {
            "types": {
                "twilio/list-picker": {
                    "body": "When do you leave?",
                    "items": [
                        {"id": "p6_a", "item": "Sat 10:45", "description": "Sat"}
                    ],
                }
            }
        }
        assert drifted_types(self.live(live), local) == ["twilio/list-picker"]

    def test_a_type_present_only_on_one_side_is_reported(self):
        """A missing fallback means some handsets render nothing at all."""
        assert drifted_types(
            self.live({"twilio/text": {"body": "hi"}}),
            {
                "types": {
                    "twilio/text": {"body": "hi"},
                    "twilio/list-picker": {"body": "hi"},
                }
            },
        ) == ["twilio/list-picker"]

    def test_every_drifted_type_is_named_not_just_the_first(self):
        assert drifted_types(
            self.live(
                {"twilio/text": {"body": "a"}, "twilio/list-picker": {"body": "b"}}
            ),
            {
                "types": {
                    "twilio/text": {"body": "A"},
                    "twilio/list-picker": {"body": "B"},
                }
            },
        ) == ["twilio/list-picker", "twilio/text"]


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


class TestDelete:
    """Deleting is what makes "created but not submitted" actually reversible.

    Twilio has no update operation for content, so revising the wording of a
    draft means deleting it and creating it again.
    """

    def deleting_client(self, *, fails=False):
        record = {}

        def do_delete():
            if fails:
                raise TwilioRestException(
                    status=404, uri="/Content", msg="gone", code=20404
                )
            record["deleted"] = True

        client = SimpleNamespace(
            content=SimpleNamespace(
                v1=SimpleNamespace(
                    contents=lambda sid: SimpleNamespace(delete=do_delete)
                )
            )
        )
        return client, record

    def test_deletes_the_template(self):
        client, record = self.deleting_client()
        delete(client, "HXdraft")
        assert record == {"deleted": True}

    def test_a_failure_is_reported_not_swallowed(self):
        client, _ = self.deleting_client(fails=True)
        with pytest.raises(TemplateError, match="Could not delete"):
            delete(client, "HXgone")


class TestSubmit:
    def test_refuses_a_list_picker(self):
        client = fake_client({"twilio/list-picker": {}})
        with pytest.raises(TemplateError, match="does not review"):
            submit(client, "HXlist", "demo_arm2_p1_en", "UTILITY")

    def test_rejects_a_bad_category_before_touching_the_api(self):
        with pytest.raises(TemplateError, match="category must be one of"):
            submit(fake_client(fails=True), "HXtext", "demo_intro", "PROMOTIONAL")

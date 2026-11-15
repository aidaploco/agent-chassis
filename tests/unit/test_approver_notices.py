"""Rendering, the sender's destination handling, and the start-up warnings."""

import logging

import httpx
import pytest

from agent_chassis.channels.base import OutboundDelivery
from agent_chassis.channels.slack import SlackOutboundSender
from agent_chassis.config import ChassisSettings
from agent_chassis.contracts import ApprovalItem
from agent_chassis.durable.activities import render_notice
from agent_chassis.durable.models import ApproverNotice
from agent_chassis.manifest import load_manifest
from agent_chassis.worker import build_approvers_sender

from pathlib import Path

EXAMPLES = Path(__file__).parents[2] / "examples"


def _notice(**fields) -> ApproverNotice:
    return ApproverNotice.model_validate(
        {
            "key": "k",
            "kind": "escalation",
            "employee": "lana",
            "tenant": "acme",
            "subject_id": "conv-1",
            "agent": "billing",
            "reason": "customer is threatening to leave",
            **fields,
        }
    )


def test_conversation_notice_says_why_who_and_where():
    text = render_notice(_notice(requester="dana", channel="slack"))
    assert "[lana] escalated a conversation to a human" in text
    assert "Reason: customer is threatening to leave" in text
    assert "Handled by agent: billing" in text
    assert "With: dana (via slack)" in text and "Conversation: conv-1" in text


def test_approval_notice_lists_calls_and_how_to_decide():
    item = ApprovalItem(call_id="call_1", tool="publish_report", args='{"title": "x"}')
    text = render_notice(
        _notice(kind="approval", subject_id="task-abc", pending=[item])
    )
    assert "needs approval to continue" in text
    assert 'publish_report {"title": "x"}  (call call_1)' in text
    assert '"workflow_id": "task-abc"' in text and "/v1/approvals" in text


def test_reminder_notice_is_labelled_as_one():
    item = ApprovalItem(call_id="c", tool="t", args="{}")
    assert "reminder" in render_notice(_notice(kind="reminder", pending=[item]))


def _deliver(sender: SlackOutboundSender, conversation_id: str) -> dict:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    sender._transport = httpx.MockTransport(handler)
    import asyncio

    asyncio.run(
        sender.deliver(
            OutboundDelivery(
                key=f"{conversation_id}:x",
                channel="slack",
                conversation_id=conversation_id,
                text="hi",
            )
        )
    )
    import json

    return json.loads(sent[0].content)


def test_a_replies_sender_still_derives_the_channel_from_the_conversation():
    assert _deliver(SlackOutboundSender("https://h/"), "slack-C42")["channel"] == "C42"


def test_an_approvers_sender_never_derives_a_channel():
    """A notice carries internal reasons: it must not be addressable to the
    customer's channel even if handed a slack-C42 conversation id."""
    payload = _deliver(
        SlackOutboundSender("https://h/", derive_channel=False), "slack-C42"
    )
    assert "channel" not in payload  # nothing addresses the customer's channel
    assert payload["text"] == "hi"


def test_an_approvers_sender_can_use_a_fixed_channel():
    sender = SlackOutboundSender(
        "https://h/", derive_channel=False, channel="#approvals"
    )
    assert _deliver(sender, "slack-C42")["channel"] == "#approvals"


@pytest.fixture
def lana():
    return load_manifest(EXAMPLES / "lana" / "manifest.yaml")


def test_http_approvers_channel_has_no_push_path_and_says_so(lana, caplog):
    with caplog.at_level(logging.WARNING, logger="agent_chassis"):
        assert build_approvers_sender(ChassisSettings(_env_file=None), lana) is None
    assert "no push path" in caplog.text and "GET /v1/tasks" in caplog.text


def test_slack_approvers_channel_without_a_webhook_warns(lana, caplog):
    lana.escalation.approvers_channel = "slack"
    with caplog.at_level(logging.WARNING, logger="agent_chassis"):
        assert build_approvers_sender(ChassisSettings(_env_file=None), lana) is None
    assert "SLACK_APPROVERS_WEBHOOK_URL is not set" in caplog.text


def test_slack_approvers_channel_with_a_webhook_builds_a_sender(lana, caplog):
    lana.escalation.approvers_channel = "slack"
    settings = ChassisSettings(
        _env_file=None, slack_approvers_webhook_url="https://hooks.example/approvers"
    )
    with caplog.at_level(logging.WARNING, logger="agent_chassis"):
        sender = build_approvers_sender(settings, lana)
    assert isinstance(sender, SlackOutboundSender) and not caplog.text

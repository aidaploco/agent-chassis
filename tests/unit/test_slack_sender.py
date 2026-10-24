import httpx

from agent_chassis.channels.base import OutboundDelivery
from agent_chassis.channels.slack import SlackOutboundSender


def _delivery(key: str) -> OutboundDelivery:
    return OutboundDelivery(
        key=key,
        channel="slack",
        conversation_id="slack-C1",
        text="hello",
    )


def _sender_with_counter() -> tuple[SlackOutboundSender, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    sender = SlackOutboundSender(
        "https://slack.example/webhook", transport=httpx.MockTransport(handler)
    )
    return sender, requests


async def test_duplicate_delivery_suppressed():
    sender, requests = _sender_with_counter()

    await sender.deliver(_delivery("slack-C1:turn:1"))
    await sender.deliver(_delivery("slack-C1:turn:1"))  # activity retry

    assert len(requests) == 1


async def test_distinct_keys_both_delivered():
    sender, requests = _sender_with_counter()

    await sender.deliver(_delivery("slack-C1:turn:1"))
    await sender.deliver(_delivery("slack-C1:turn:2"))
    await sender.deliver(_delivery("slack-C1:reminder:esc-1:1"))

    assert len(requests) == 3
    body = requests[0].read()
    assert b"idempotency_key" in body and b"slack-C1:turn:1" in body


async def test_seen_keys_are_bounded():
    sender, requests = _sender_with_counter()
    sender._max_seen = 2

    for n in range(4):
        await sender.deliver(_delivery(f"slack-C1:turn:{n}"))
    # Oldest key evicted from the LRU → redelivery is possible (documented
    # bound), newest keys still suppressed.
    await sender.deliver(_delivery("slack-C1:turn:3"))
    assert len(requests) == 4

"""Slack-shaped webhook channel — async: the webhook is acknowledged
immediately and the reply is pushed back through an OutboundSender. Tested
against a fake; a real Slack integration would swap the sender's target URL
and auth, nothing else."""

import asyncio
import hashlib
import hmac
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from temporalio.client import Client

from agent_chassis.channels.base import OutboundDelivery
from agent_chassis.config import get_settings
from agent_chassis.contracts import ContentPart, MessageEnvelope
from agent_chassis.utils.logger import get_logger

CHANNEL = "slack"
_logger = get_logger(__name__)


class SlackEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")  # Slack payloads carry extras

    channel: str
    user: str
    text: str


def conversation_id_for(event: SlackEvent) -> str:
    return f"slack-{event.channel}"


_SIGNATURE_MAX_AGE_SECONDS = 300


def verify_signature(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Slack signing-secret scheme: v0=(HMAC-SHA256 of "v0:{ts}:{body}"),
    with a freshness window against replay."""
    try:
        if abs(time.time() - float(timestamp)) > _SIGNATURE_MAX_AGE_SECONDS:
            return False
    except ValueError:
        return False
    expected = (
        "v0="
        + hmac.new(
            secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256
        ).hexdigest()
    )
    return hmac.compare_digest(expected, signature)


def create_router(get_client: Callable[[], Awaitable[Client]]) -> APIRouter:
    router = APIRouter()
    background: set[asyncio.Task] = set()

    @router.post("/v1/webhooks/slack")
    async def slack_webhook(request: Request) -> dict[str, bool]:
        """Ack fast; the turn runs in the background and the reply is pushed
        via the worker's outbound sender, not this response. Inbound
        authenticity (D21): HMAC-verified when SLACK_SIGNING_SECRET is set;
        unsigned webhooks are a logged local-dev posture, never silent."""
        from agent_chassis.channels.http import send_to_conversation

        settings = get_settings()
        body = await request.body()
        if settings.slack_signing_secret:
            if not verify_signature(
                settings.slack_signing_secret,
                request.headers.get("X-Slack-Request-Timestamp", ""),
                body,
                request.headers.get("X-Slack-Signature", ""),
            ):
                raise HTTPException(status_code=401, detail="invalid Slack signature")
        else:
            _logger.warning(
                "unsigned Slack webhook accepted — SLACK_SIGNING_SECRET not set"
            )
        try:
            event = SlackEvent.model_validate_json(body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        envelope = MessageEnvelope(
            sender=event.user,
            tenant=settings.tenant,
            conversation_id=conversation_id_for(event),
            channel=CHANNEL,
            parts=[ContentPart(text=event.text)],
        )

        async def run() -> None:
            try:
                client = await get_client()
                await send_to_conversation(
                    client, settings, envelope, tenant=settings.tenant
                )
            except Exception:
                _logger.exception(
                    "slack turn failed conversation=%s", envelope.conversation_id
                )

        task = asyncio.create_task(run())
        background.add(task)
        task.add_done_callback(background.discard)
        return {"ok": True}

    return router


class SlackOutboundSender:
    """Posts replies to a Slack-compatible webhook URL.

    Idempotent per delivery key (D15): a bounded LRU of seen keys suppresses
    duplicates caused by at-least-once activity execution.
    """

    def __init__(
        self,
        webhook_url: str,
        max_seen: int = 4096,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        derive_channel: bool = True,
        channel: str | None = None,
    ) -> None:
        """
        Args:
            webhook_url: The Slack-compatible incoming webhook to post to.
            derive_channel: Post to the channel named by the delivery's
                conversation id (``slack-C42`` -> ``C42``). Right for replies;
                wrong for anything that is not addressed to a conversation,
                which must pass ``False`` so nothing can land in a customer's
                channel by accident.
            channel: A fixed channel to post to instead (only used when
                ``derive_channel`` is False). Unset omits the field, so the
                webhook's own channel applies.
        """
        self._webhook_url = webhook_url
        self._derive_channel = derive_channel
        self._fixed_channel = channel
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._max_seen = max_seen
        self._transport = transport

    async def deliver(self, delivery: OutboundDelivery) -> None:
        if delivery.key in self._seen:
            self._seen.move_to_end(delivery.key)
            _logger.info("duplicate delivery suppressed key=%s", delivery.key)
            return
        # Reserve the key while the send is in flight (a concurrent retry of
        # the same delivery is still suppressed), but forget it if the send
        # fails: otherwise the activity's retry is "suppressed as a duplicate"
        # and the reply is silently lost — at-most-once, not at-least-once.
        self._remember(delivery.key)
        try:
            async with httpx.AsyncClient(
                timeout=10, transport=self._transport
            ) as client:
                payload: dict = {
                    "text": delivery.text,
                    "metadata": {"idempotency_key": delivery.key},
                }
                if self._derive_channel:
                    payload["channel"] = delivery.conversation_id.removeprefix("slack-")
                elif self._fixed_channel:
                    payload["channel"] = self._fixed_channel
                response = await client.post(self._webhook_url, json=payload)
                response.raise_for_status()
        except BaseException:
            self._seen.pop(delivery.key, None)
            raise

    def _remember(self, key: str) -> None:
        self._seen[key] = None
        while len(self._seen) > self._max_seen:
            self._seen.popitem(last=False)

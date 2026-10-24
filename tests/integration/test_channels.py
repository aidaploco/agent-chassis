"""Channel adapters end-to-end: HTTP (sync, reply in the response) and the
Slack-shaped webhook (async ack + pushed reply with idempotency key)."""

import asyncio
import hashlib
import hmac
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from agent_chassis.channels.http import create_app
from agent_chassis.channels.slack import create_router
from agent_chassis.config import get_settings

# Shared fixtures/fakes: same chassis (worker + dev server + FunctionModel).
from tests.integration.test_conversation_workflow import (  # noqa: F401
    FAKE_DELIVERIES,
    chassis,
    env,
    settings,
)


def _api_fixture(env_extra: dict[str, str] | None = None):
    async def fixture(chassis, monkeypatch):  # noqa: F811
        client, task_queue, _ = chassis
        monkeypatch.setenv("TEMPORAL_TASK_QUEUE", task_queue)
        for key, value in (env_extra or {}).items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

        async def get_client():
            return client

        app = create_app(get_client)
        app.include_router(create_router(get_client))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http
        get_settings.cache_clear()  # env reverts at teardown; drop stale cache

    return fixture


api = pytest.fixture(_api_fixture())
# D21 posture: tokens + Slack signing secret configured.
authed_api = pytest.fixture(
    _api_fixture(
        {
            "HTTP_BEARER_TOKENS": "tok-a:aida,tok-b:bob",
            "SLACK_SIGNING_SECRET": "sh-test",
        }
    )
)


async def test_healthz(api):
    response = await api.get("/healthz")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


async def test_http_channel_round_trip(api):
    conversation = str(uuid.uuid4())

    response = await api.post(
        "/v1/messages",
        json={"conversation_id": conversation, "message": "hello over http"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "hello over http" in body["content"]
    assert body["agent"] == "supervisor" and body["turn_number"] == 1
    assert body["conversation_id"] == conversation


async def test_slack_channel_acks_then_pushes_reply(api):
    response = await api.post(
        "/v1/webhooks/slack",
        json={"channel": "C42", "user": "U1", "text": "hi from slack"},
    )
    assert response.status_code == 200 and response.json() == {"ok": True}

    async def delivered():
        while not FAKE_DELIVERIES:
            await asyncio.sleep(0.05)

    await asyncio.wait_for(delivered(), timeout=15)
    delivery = FAKE_DELIVERIES[0]
    assert delivery.key == "slack-C42:turn:1"
    assert delivery.channel == "slack"
    assert "hi from slack" in delivery.text


# -- D21: channel auth --------------------------------------------------------


async def test_identity_endpoints_disabled_without_auth(api):
    """No HTTP_BEARER_TOKENS → approvals/tasks refuse to serve; messages
    stay open for local dev (covered by the round-trip test above)."""
    task = await api.post("/v1/tasks", json={"task": "daily_check"})
    approval = await api.post(
        "/v1/approvals", json={"workflow_id": "x", "approved": True}
    )
    assert task.status_code == 503 and approval.status_code == 503


async def test_bad_token_rejected_everywhere(authed_api):
    for call in (
        authed_api.post("/v1/tasks", json={"task": "daily_check"}),
        authed_api.post("/v1/messages", json={"conversation_id": "c", "message": "hi"}),
    ):
        response = await call
        assert response.status_code == 401


async def test_authenticated_approval_binds_principal(authed_api, chassis):  # noqa: F811
    """Full HTTP loop: start task, see the pause, approve — the audited
    approver is the token's principal, not anything the body could say."""
    _, _, activities = chassis
    headers = {"Authorization": "Bearer tok-a"}
    started = await authed_api.post(
        "/v1/tasks", json={"task": "daily_check"}, headers=headers
    )
    assert started.status_code == 200
    workflow_id = started.json()["workflow_id"]

    async def wait_pending():
        while True:
            status = (
                await authed_api.get(f"/v1/tasks/{workflow_id}", headers=headers)
            ).json()
            if status.get("state") == "waiting_on_human":
                return status
            await asyncio.sleep(0.1)

    await asyncio.wait_for(wait_pending(), timeout=20)
    approved = await authed_api.post(
        "/v1/approvals",
        json={"workflow_id": workflow_id, "approved": True, "comment": "ok"},
        headers=headers,
    )
    assert approved.status_code == 200
    assert approved.json()["approver"] == "aida"

    async def wait_done():
        while True:
            body = (
                await authed_api.get(f"/v1/tasks/{workflow_id}", headers=headers)
            ).json()
            if "content" in body:
                return body
            await asyncio.sleep(0.1)

    reply = await asyncio.wait_for(wait_done(), timeout=20)
    assert "published" in reply["content"]
    assert activities.approval_records[-1].approver == "aida"


async def test_slack_signature_enforced(authed_api):
    body = b'{"channel": "C1", "user": "U1", "text": "hello"}'
    unsigned = await authed_api.post(
        "/v1/webhooks/slack",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert unsigned.status_code == 401

    timestamp = str(int(time.time()))
    digest = hmac.new(
        b"sh-test", b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
    ).hexdigest()
    signed = await authed_api.post(
        "/v1/webhooks/slack",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": f"v0={digest}",
        },
    )
    assert signed.status_code == 200 and signed.json() == {"ok": True}


def test_stale_slack_timestamp_rejected():
    from agent_chassis.channels.slack import verify_signature

    old = str(int(time.time()) - 3600)
    digest = hmac.new(
        b"sh-test", f"v0:{old}:".encode() + b"{}", hashlib.sha256
    ).hexdigest()
    assert not verify_signature("sh-test", old, b"{}", f"v0={digest}")

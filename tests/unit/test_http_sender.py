"""D21 on /v1/messages: the envelope's sender is the authenticated principal,
never the request body, once auth is configured."""

from httpx import ASGITransport, AsyncClient

from agent_chassis.channels import http
from agent_chassis.durable.models import TurnReply


async def _post(monkeypatch, body: dict, headers: dict | None = None):
    sent = []

    async def fake_send(client, settings, envelope, tenant):
        sent.append(envelope)
        return TurnReply(content="ok", agent="supervisor", turn_number=1)

    async def get_client():
        return None

    monkeypatch.setattr(http, "send_to_conversation", fake_send)
    app = http.create_app(get_client)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/messages", json=body, headers=headers)
    return response, sent


async def test_authenticated_sender_is_the_principal(monkeypatch):
    monkeypatch.setenv("HTTP_BEARER_TOKENS", "tok-a:aida")
    response, sent = await _post(
        monkeypatch,
        {"conversation_id": "c-1", "message": "hi", "sender": "mallory"},
        headers={"Authorization": "Bearer tok-a"},
    )
    assert response.status_code == 200
    assert sent[0].sender == "aida"  # the body cannot name someone else


async def test_anonymous_posture_keeps_the_body_sender(monkeypatch):
    monkeypatch.setenv("HTTP_BEARER_TOKENS", "")
    response, sent = await _post(
        monkeypatch, {"conversation_id": "c-1", "message": "hi", "sender": "dana"}
    )
    assert response.status_code == 200
    assert sent[0].sender == "dana"

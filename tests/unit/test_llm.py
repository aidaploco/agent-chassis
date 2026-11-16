"""utils/llm.py drives two vendor SDKs directly, so a major-version bump can
break it without a single unit test noticing. These run the real code path
against a mock transport: no network, no API key."""

import json

import httpx2
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from agent_chassis.utils import llm


@pytest.fixture
def anthropic_requests(monkeypatch):
    """Route llm.complete's Anthropic client to a mock; record what it sends."""
    seen: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": "PASS"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = AsyncAnthropic(
        api_key="test",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    monkeypatch.setattr(llm, "_anthropic", lambda: client)
    return seen


async def test_anthropic_path_works_with_the_installed_sdk(anthropic_requests):
    """Regression: the call passed temperature=0, which anthropic 2.x rejects
    with a TypeError, so every anthropic: guardrail / judge call would crash."""
    text = await llm.complete(
        "anthropic:claude-test", "be terse", "hello", max_tokens=5
    )

    assert text == "PASS"
    (body,) = anthropic_requests
    assert body["model"] == "claude-test" and body["max_tokens"] == 5
    assert body["system"] == "be terse"
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert "temperature" not in body


async def test_anthropic_default_max_tokens(anthropic_requests):
    await llm.complete("anthropic:claude-test", "s", "u")
    assert anthropic_requests[0]["max_tokens"] == 1024


@pytest.fixture
def openai_requests(monkeypatch):
    seen: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "c1",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "FLAG"},
                    }
                ],
            },
        )

    client = AsyncOpenAI(
        api_key="test",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    monkeypatch.setattr(llm, "_openai", lambda: client)
    return seen


async def test_openai_path_pins_temperature_and_can_force_json(openai_requests):
    text = await llm.complete(
        "openai:gpt-test", "judge", "convo", max_tokens=7, force_json=True
    )

    assert text == "FLAG"
    (body,) = openai_requests
    assert body["model"] == "gpt-test" and body["temperature"] == 0
    assert body["max_completion_tokens"] == 7
    assert body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_a_bare_model_name_means_openai():
    assert llm._split("gpt-5.4-mini") == ("openai", "gpt-5.4-mini")
    assert llm._split("anthropic:claude-x") == ("anthropic", "claude-x")

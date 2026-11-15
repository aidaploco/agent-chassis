"""Eval runner (D17) against a stubbed Employee: no server, no model."""

import httpx
import pytest

from agent_chassis.evals import runner
from agent_chassis.evals.cases import EvalCase, EvalCaseError, load_cases


def _case(**fields) -> EvalCase:
    return EvalCase.model_validate(
        {"id": "c", "messages": [{"role": "user", "content": "hello"}], **fields}
    )


def _employee(status: int = 200, **reply):
    """A transport that answers /v1/messages like the HTTP channel does."""
    requests: list[httpx.Request] = []
    body = {
        "conversation_id": "x",
        "content": "hi there",
        "agent": "support",
        "turn_number": 1,
        "escalated": False,
        "guardrail_tripped": None,
        "tools_called": [],
        **reply,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler), requests


@pytest.fixture
def judge(monkeypatch):
    replies = {"text": '{"reasoning": "fine", "pass": true}'}

    async def fake_complete(model, system, user, **kwargs) -> str:
        if isinstance(replies["text"], Exception):
            raise replies["text"]
        return replies["text"]

    monkeypatch.setattr(runner, "complete", fake_complete)
    return replies


async def test_structural_and_quality_grades(judge):
    transport, requests = _employee(tools_called=["create_ticket"])
    result = await runner.run_case(
        _case(
            expected_agent="support",
            expected_tools=["create_ticket"],
            quality_prompt="q",
        ),
        "http://employee",
        "openai:judge",
        transport=transport,
    )
    assert result.passed
    assert [g.name for g in result.graders] == ["agent", "tools", "quality"]
    assert "authorization" not in requests[0].headers


async def test_bearer_token_is_sent(judge):
    transport, requests = _employee()
    await runner.run_case(
        _case(), "http://employee", None, token="tok-a", transport=transport
    )
    assert requests[0].headers["authorization"] == "Bearer tok-a"


async def test_http_failure_fails_the_case_not_the_run(judge):
    """A 401/503 used to raise out of run_case and abort the whole suite."""
    transport, _ = _employee(status=503)
    result = await runner.run_case(
        _case(expected_agent="support", quality_prompt="q"),
        "http://employee",
        "openai:judge",
        transport=transport,
    )
    assert not result.passed
    assert [g.name for g in result.graders] == ["execution"]  # no judge call
    assert "503" in result.graders[0].detail


@pytest.mark.parametrize(
    ("reply", "passed"),
    [
        ('{"reasoning": "ok", "pass": true}', True),
        ('Here you go:\n```json\n{"reasoning": "ok", "pass": true}\n```', True),
        ('{"reasoning": "invented a refund", "pass": false}', False),
        ("I think this passes.", False),
        ("", False),
        ('{"pass": "yes"}', False),
    ],
)
async def test_judge_reply_is_parsed_or_failed_never_raised(judge, reply, passed):
    judge["text"] = reply
    transport, _ = _employee()
    result = await runner.run_case(
        _case(quality_prompt="q"),
        "http://employee",
        "anthropic:judge",
        transport=transport,
    )
    assert result.graders[-1].name == "quality"
    assert result.graders[-1].passed is passed


async def test_judge_error_is_a_failed_grade(judge):
    judge["text"] = RuntimeError("rate limited")
    transport, _ = _employee()
    result = await runner.run_case(
        _case(quality_prompt="q"),
        "http://employee",
        "openai:judge",
        transport=transport,
    )
    assert not result.passed and "rate limited" in result.graders[-1].detail


def test_route_is_not_an_expected_directive(tmp_path):
    """It was accepted but could never pass: the channel reports how a turn
    ended (reply/escalate), and a route never ends a turn."""
    (tmp_path / "bad.yaml").write_text(
        "- id: x\n  messages: [{role: user, content: a}]\n  expected_directive: route\n"
    )
    with pytest.raises(EvalCaseError, match="expected_directive"):
        load_cases(tmp_path)


# -- run_suite: repeat + per-case pass-rate gate ------------------------------


def _suite_cases(tmp_path) -> str:
    (tmp_path / "cases.yaml").write_text(
        "- id: steady\n  messages: [{role: user, content: a}]\n"
        "- id: flaky\n  messages: [{role: user, content: b}]\n"
    )
    return tmp_path


def _scripted(monkeypatch, outcomes: dict[str, list[bool]]):
    """Make run_case pass or fail per case according to a script."""
    seen: dict[str, int] = {}

    async def fake_run_case(case, base_url, judge_model, **kwargs):
        n = seen.get(case.id, 0)
        seen[case.id] = n + 1
        ok = outcomes[case.id][n]
        return runner.CaseResult(
            case.id,
            ok,
            [runner.GraderResult("agent", ok, "wrong agent")],
        )

    monkeypatch.setattr(runner, "run_case", fake_run_case)
    return seen


async def test_single_run_is_strict(tmp_path, monkeypatch, capsys):
    _scripted(monkeypatch, {"steady": [True], "flaky": [False]})
    code = await runner.run_suite(_suite_cases(tmp_path), "http://x", None)
    out = capsys.readouterr().out
    assert code == 1
    assert "PASS steady\n" in out and "FAIL flaky\n" in out
    assert "1/2 cases passed" in out


async def test_repeat_tolerates_an_occasional_miss(tmp_path, monkeypatch, capsys):
    seen = _scripted(
        monkeypatch,
        {"steady": [True, True, True], "flaky": [True, False, True]},
    )
    code = await runner.run_suite(
        _suite_cases(tmp_path), "http://x", None, repeat=3, min_pass_rate=0.66
    )
    out = capsys.readouterr().out
    assert code == 0 and seen == {"steady": 3, "flaky": 3}
    assert "PASS flaky (2/3)" in out and "PASS steady (3/3)" in out


async def test_repeat_still_fails_a_broken_case(tmp_path, monkeypatch, capsys):
    _scripted(
        monkeypatch,
        {"steady": [True, True, True], "flaky": [False, True, False]},
    )
    code = await runner.run_suite(
        _suite_cases(tmp_path), "http://x", None, repeat=3, min_pass_rate=0.66
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL flaky (1/3)" in out and "needs 66% of runs, got 33%" in out
    assert "agent: wrong agent" in out  # why the failing runs failed
    assert "1/2 cases passed" in out

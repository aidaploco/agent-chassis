import pytest

from agent_chassis.evals import (
    EvalExecution,
    grade_structural,
    load_cases,
)
from agent_chassis.evals.cases import EvalCaseError

CASES_YAML = """
- id: billing_invoice
  messages:
    - role: user
      content: "Can I see my latest invoice?"
  expected_agent: billing
  expected_tools: [get_invoice]
  expected_directive: reply
  quality_prompt: "States the invoice details clearly."

- id: multi_turn_context
  messages:
    - role: user
      content: "What plans do you offer?"
    - role: assistant
      content: "Starter, Pro and Enterprise."
    - role: user
      content: "How many seats does the middle one include?"
  expected_directive: reply
  guardrail_expectations:
    off_topic: pass
"""


@pytest.fixture
def cases(tmp_path):
    (tmp_path / "cases.yaml").write_text(CASES_YAML)
    return load_cases(tmp_path)


def test_load_cases_supports_multi_turn(cases):
    multi = cases[1]
    assert [m.role for m in multi.messages] == ["user", "assistant", "user"]
    assert multi.last_user_message.startswith("How many seats")


def test_last_message_must_be_user(tmp_path):
    (tmp_path / "bad.yaml").write_text(
        "- id: x\n  messages:\n    - role: assistant\n      content: hi\n"
    )
    with pytest.raises(EvalCaseError, match="last message must be from the user"):
        load_cases(tmp_path)


def test_duplicate_ids_rejected(tmp_path):
    (tmp_path / "dup.yaml").write_text(
        "- id: x\n  messages: [{role: user, content: a}]\n"
        "- id: x\n  messages: [{role: user, content: b}]\n"
    )
    with pytest.raises(EvalCaseError, match="duplicate case id"):
        load_cases(tmp_path)


def test_unknown_case_field_rejected(tmp_path):
    (tmp_path / "bad.yaml").write_text(
        "- id: x\n  messages: [{role: user, content: a}]\n  judge: gpt-4o\n"
    )
    with pytest.raises(EvalCaseError, match="judge"):
        load_cases(tmp_path)


def test_structural_graders_pass(cases):
    execution = EvalExecution(
        final_agent="billing",
        directive="reply",
        final_output="Your invoice is INV-1.",
        tools_called=["get_invoice"],
    )
    results = grade_structural(cases[0], execution)
    assert [r.name for r in results] == ["agent", "directive", "tools"]
    assert all(r.passed for r in results)


def test_structural_graders_catch_wrong_agent_and_missing_tool(cases):
    execution = EvalExecution(
        final_agent="support",
        directive="reply",
        final_output="hi",
    )
    by_name = {r.name: r for r in grade_structural(cases[0], execution)}
    assert not by_name["agent"].passed
    assert not by_name["tools"].passed
    assert by_name["directive"].passed


def test_guardrail_expectations_graded(cases):
    execution = EvalExecution(
        final_agent="support",
        directive="reply",
        final_output="10 seats.",
        guardrails_tripped=["off_topic"],
    )
    by_name = {r.name: r for r in grade_structural(cases[1], execution)}
    assert not by_name["guardrail:off_topic"].passed


def test_execution_error_short_circuits(cases):
    results = grade_structural(
        cases[0], EvalExecution("", "", "", error="worker unreachable")
    )
    assert len(results) == 1 and not results[0].passed

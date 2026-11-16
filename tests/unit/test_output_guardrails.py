"""Builtin output guardrails (D5) and how the registry sorts guardrails by stage.
All deterministic: no model is involved."""

from pathlib import Path

import pytest
import yaml

from agent_chassis.manifest import RoleManifest
from agent_chassis.packs import (
    GuardrailPack,
    OutputGuardrailPack,
    OutputReply,
    PackError,
    PackRegistry,
)
from agent_chassis.packs.guardrails import BUILTIN_GUARDRAILS
from agent_chassis.packs.output_guardrails import (
    BUILTIN_OUTPUT_GUARDRAILS,
    PromptLeakConfig,
    PromptLeakGuardrail,
    ReplyLengthConfig,
    ReplyLengthGuardrail,
    SecretLeakConfig,
    SecretLeakGuardrail,
    _known_secrets,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _reply(text: str, instructions: str = "") -> OutputReply:
    return OutputReply(
        text=text, agent="supervisor", user_message="hi", instructions=instructions
    )


async def test_reply_length_trips_only_over_the_limit():
    pack, cfg = ReplyLengthGuardrail(), ReplyLengthConfig(max_chars=200)
    assert (await pack.check(_reply("x" * 200), cfg)).passed
    verdict = await pack.check(_reply("x" * 201), cfg)
    assert not verdict.passed and "201" in (verdict.reason or "")


def test_reply_length_bounds_are_enforced():
    with pytest.raises(ValueError):
        ReplyLengthConfig(max_chars=10)


async def test_secret_leak_catches_a_held_secret_without_quoting_it(monkeypatch):
    secret = "s3cr3t-value-0123456789"
    monkeypatch.setenv("SOME_API_KEY", secret)
    verdict = await SecretLeakGuardrail().check(
        _reply(f"the key is {secret}, enjoy"), SecretLeakConfig()
    )
    assert not verdict.passed
    assert secret not in (verdict.reason or "") + str(verdict.detail)
    assert verdict.detail == {"source": "SOME_API_KEY"}


async def test_secret_leak_catches_a_connection_string_password(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://app:hunter2hunter2@db:5432/x")
    verdict = await SecretLeakGuardrail().check(
        _reply("password is hunter2hunter2"), SecretLeakConfig()
    )
    assert not verdict.passed


# Assembled at runtime: a literal shaped like a real credential would trip the
# repository's own secret scanner, even though every value here is made up.
@pytest.mark.parametrize(
    "leak",
    [
        "sk-" + "proj-" + "a" * 24,
        "xox" + "b-" + "1234567890-" + "abcdefghij",
        "gh" + "p_" + "a" * 30,
        "AK" + "IA" + "A" * 16,
        "-----BEGIN RSA " + "PRIVATE KEY-----",
        "https://hooks." + "slack.com/services/" + "T000/B000/" + "X" * 8,
    ],
)
async def test_secret_leak_patterns(leak, monkeypatch):
    monkeypatch.delenv("SOME_API_KEY", raising=False)
    verdict = await SecretLeakGuardrail().check(
        _reply(f"here: {leak}"), SecretLeakConfig(check_environment=False)
    )
    assert not verdict.passed


async def test_secret_leak_passes_ordinary_replies(monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "short")  # too short to be a safe needle
    verdict = await SecretLeakGuardrail().check(
        _reply("Your invoice INV-1042 is due on the 1st; use the key short."),
        SecretLeakConfig(),
    )
    assert verdict.passed


def test_known_secrets_ignores_hosts_and_unrelated_variables():
    found = _known_secrets(
        {
            "DATABASE_URL": "postgresql://db:5432/x",  # no password
            "TEMPORAL_ADDRESS": "temporal:7233",
            "OPENAI_API_KEY": "sk-abcdefghijklmnop",
            "EMPTY_TOKEN": "",
        }
    )
    assert list(found) == ["OPENAI_API_KEY"]


PROMPT = (
    "You are Lana, a finance operations assistant. Always answer billing "
    "questions using the knowledge base and never invent figures, quote policy "
    "text only when a customer asks for it, and escalate refunds over the "
    "approval threshold to a human teammate immediately."
)


async def test_prompt_leak_trips_on_a_long_verbatim_run():
    cfg = PromptLeakConfig(min_run_words=12)
    reply = "Sure! My instructions say: " + PROMPT + " Anything else?"
    verdict = await PromptLeakGuardrail().check(_reply(reply, PROMPT), cfg)
    assert not verdict.passed and verdict.detail == {"min_run_words": 12}


async def test_prompt_leak_ignores_short_shared_phrases():
    cfg = PromptLeakConfig(min_run_words=12)
    reply = "I will escalate refunds over the approval threshold for you."
    assert (await PromptLeakGuardrail().check(_reply(reply, PROMPT), cfg)).passed


async def test_prompt_leak_is_case_and_punctuation_insensitive():
    cfg = PromptLeakConfig(min_run_words=12)
    mangled = PROMPT.upper().replace(",", " --")
    assert not (await PromptLeakGuardrail().check(_reply(mangled, PROMPT), cfg)).passed


async def test_prompt_leak_passes_when_there_is_no_prompt():
    assert (
        await PromptLeakGuardrail().check(_reply(PROMPT, ""), PromptLeakConfig())
    ).passed


def test_builtin_names_are_unique_across_stages():
    names = [p.name for p in (*BUILTIN_GUARDRAILS, *BUILTIN_OUTPUT_GUARDRAILS)]
    assert len(names) == len(set(names))
    assert all(issubclass(p, OutputGuardrailPack) for p in BUILTIN_OUTPUT_GUARDRAILS)


def _manifest(guardrails: list[dict]) -> RoleManifest:
    data = yaml.safe_load((FIXTURES / "lana.yaml").read_text())
    data["guardrails"] = guardrails
    return RoleManifest.model_validate(data)


def _registry() -> PackRegistry:
    registry = PackRegistry()
    for pack in (*BUILTIN_GUARDRAILS, *BUILTIN_OUTPUT_GUARDRAILS):
        registry.register(pack())
    return registry


def test_resolve_sorts_guardrails_by_stage_in_manifest_order():
    registry = _registry()
    manifest = _manifest(
        [
            {"pack": "secret_leak", "config": {}},
            {"pack": "message_length", "config": {}},
            {"pack": "reply_length", "config": {"max_chars": 500}},
        ]
    )
    # The fixture's capability packs aren't registered here; this test is about
    # guardrails only.
    manifest = manifest.model_copy(update={"capabilities": []})
    resolved = registry.resolve(manifest)
    assert [g.pack.name for g in resolved.input_guardrails] == ["message_length"]
    assert [g.pack.name for g in resolved.output_guardrails] == [
        "secret_leak",
        "reply_length",
    ]
    assert all(isinstance(g.pack, GuardrailPack) for g in resolved.input_guardrails)
    # An override on an output pack is audited like any other.
    assert any(
        o.pack == "reply_length" and o.setting == "max_chars"
        for o in resolved.overrides
    )


def test_a_pack_name_cannot_exist_on_both_sides():
    registry = _registry()

    class Clash(OutputGuardrailPack):
        name = "message_length"

        async def check(self, reply, config):  # pragma: no cover
            raise NotImplementedError

    with pytest.raises(PackError):
        registry.register(Clash())

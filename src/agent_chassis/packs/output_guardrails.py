"""Builtin output guardrails (D5): checks on a final reply before it reaches
the user.

They are deterministic on purpose. An output check runs on every reply, so an
LLM judge here would double the model cost and latency of a turn and add a
probabilistic false-positive rate to every answer. These three catch the
failures that have a mechanical signature: a runaway reply, a leaked
credential, a recited system prompt.
"""

import os
import re
from collections.abc import Mapping
from typing import ClassVar
from urllib.parse import urlsplit

from pydantic import Field

from agent_chassis.packs.base import (
    GuardrailVerdict,
    OutputGuardrailPack,
    OutputReply,
    PackConfig,
)


def _expect[C: PackConfig](config: PackConfig, model: type[C]) -> C:
    if not isinstance(config, model):
        raise TypeError(f"expected {model.__name__}, got {type(config).__name__}")
    return config


class ReplyLengthConfig(PackConfig):
    max_chars: int = Field(default=4000, ge=200, le=50_000)


class ReplyLengthGuardrail(OutputGuardrailPack):
    name: ClassVar[str] = "reply_length"
    config_model: ClassVar[type[PackConfig]] = ReplyLengthConfig

    async def check(self, reply: OutputReply, config: PackConfig) -> GuardrailVerdict:
        cfg = _expect(config, ReplyLengthConfig)
        if len(reply.text) > cfg.max_chars:
            return GuardrailVerdict(
                passed=False,
                reason=f"reply is {len(reply.text)} characters (limit {cfg.max_chars})",
                detail={"chars": len(reply.text), "max_chars": cfg.max_chars},
            )
        return GuardrailVerdict(passed=True)


# Shapes of credentials that are recognisable without knowing the value.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api key (sk-)", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("github token", re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("slack webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+")),
)
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|WEBHOOK|CODEC", re.IGNORECASE)
_MIN_SECRET_LEN = 12  # shorter values collide with ordinary words


def _known_secrets(environ: Mapping[str, str]) -> dict[str, str]:
    """Values the process holds that must never appear in a reply, keyed by the
    variable that holds them (the key is what gets reported, never the value)."""
    found: dict[str, str] = {}
    for name, value in environ.items():
        if not value:
            continue
        if _SECRET_NAME.search(name) and len(value) >= _MIN_SECRET_LEN:
            found[name] = value
        elif name.endswith("_URL"):
            # A connection string's password is the secret, the host is not.
            try:
                password = urlsplit(value).password
            except ValueError:
                password = None
            if password and len(password) >= 8:
                found[name] = password
    return found


class SecretLeakConfig(PackConfig):
    check_environment: bool = True
    check_patterns: bool = True


class SecretLeakGuardrail(OutputGuardrailPack):
    """Trips when a reply contains a credential: a value this process holds in
    an environment variable, or anything shaped like a well-known token."""

    name: ClassVar[str] = "secret_leak"
    config_model: ClassVar[type[PackConfig]] = SecretLeakConfig

    async def check(self, reply: OutputReply, config: PackConfig) -> GuardrailVerdict:
        cfg = _expect(config, SecretLeakConfig)
        if cfg.check_environment:
            for var, value in _known_secrets(os.environ).items():
                if value in reply.text:
                    return GuardrailVerdict(
                        passed=False,
                        reason=f"reply contains the value of {var}",
                        detail={"source": var},
                    )
        if cfg.check_patterns:
            for label, pattern in _SECRET_PATTERNS:
                if pattern.search(reply.text):
                    return GuardrailVerdict(
                        passed=False,
                        reason=f"reply contains a {label}",
                        detail={"pattern": label},
                    )
        return GuardrailVerdict(passed=True)


_WORD = re.compile(r"[a-z0-9']+")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class PromptLeakConfig(PackConfig):
    # How many consecutive words a reply may share with the agent's
    # instructions before it counts as reciting them.
    min_run_words: int = Field(default=30, ge=8, le=200)


class PromptLeakGuardrail(OutputGuardrailPack):
    """Trips when a reply reproduces a long verbatim run of the agent's own
    instructions (the persona, pack fragments and routing contract)."""

    name: ClassVar[str] = "prompt_leak"
    config_model: ClassVar[type[PackConfig]] = PromptLeakConfig

    async def check(self, reply: OutputReply, config: PackConfig) -> GuardrailVerdict:
        cfg = _expect(config, PromptLeakConfig)
        n = cfg.min_run_words
        prompt_words = _words(reply.instructions)
        reply_words = _words(reply.text)
        if len(prompt_words) < n or len(reply_words) < n:
            return GuardrailVerdict(passed=True)
        grams = {
            tuple(prompt_words[i : i + n]) for i in range(len(prompt_words) - n + 1)
        }
        for i in range(len(reply_words) - n + 1):
            if tuple(reply_words[i : i + n]) in grams:
                return GuardrailVerdict(
                    passed=False,
                    reason=f"reply recites {n}+ consecutive words of the agent's instructions",
                    detail={"min_run_words": n},
                )
        return GuardrailVerdict(passed=True)


BUILTIN_OUTPUT_GUARDRAILS = (
    ReplyLengthGuardrail,
    SecretLeakGuardrail,
    PromptLeakGuardrail,
)

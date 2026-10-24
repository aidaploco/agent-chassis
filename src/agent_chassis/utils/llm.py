"""Chassis-owned single-shot completion helper.

For chassis-side LLM calls that must survive a runtime swap — guardrail
verdicts (D5) and the eval judge (D17) — and therefore cannot go through the
agent adapter (import-linter forbids pydantic_ai here). Model strings use the
same provider-prefixed form as the tier table ("openai:gpt-4o-mini",
"anthropic:claude-opus-4-8"); a bare name means openai. One client per
provider per process.
"""

from functools import lru_cache

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

_DEFAULT_MAX_TOKENS = 1024


@lru_cache(maxsize=1)
def _openai() -> AsyncOpenAI:
    return AsyncOpenAI()


@lru_cache(maxsize=1)
def _anthropic() -> AsyncAnthropic:
    return AsyncAnthropic()


def _split(model: str) -> tuple[str, str]:
    provider, sep, name = model.partition(":")
    if not sep:
        return "openai", model
    return provider, name


async def complete(
    model: str,
    system: str,
    user: str,
    *,
    max_tokens: int | None = None,
    force_json: bool = False,
) -> str:
    """One system+user turn, deterministic (temperature 0), text back.

    Args:
        model: Provider-prefixed model string (``openai:gpt-4o-mini``,
            ``anthropic:claude-opus-4-8``); a bare name means openai.
        system: The system prompt.
        user: The user message.
        max_tokens: Cap on the response length; provider defaults apply when
            ``None``.
        force_json: Request a JSON object back. Maps to OpenAI's
            ``json_object`` response format; Anthropic has no equivalent
            switch, so there the prompt's own JSON instruction is the contract
            (callers must ask for JSON in the prompt regardless).

    Returns:
        The model's text output (empty string if it returned none).

    Raises:
        ValueError: If ``model`` names an unsupported provider.
    """
    provider, name = _split(model)
    if provider == "openai":
        response = await _openai().chat.completions.create(
            model=name,
            temperature=0,
            max_completion_tokens=max_tokens,
            response_format={"type": "json_object"} if force_json else {"type": "text"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""
    if provider == "anthropic":
        response = await _anthropic().messages.create(
            model=name,
            max_tokens=max_tokens or _DEFAULT_MAX_TOKENS,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if block.type == "text")
    raise ValueError(
        f"unsupported model provider {provider!r} in {model!r}"
        " (expected openai: or anthropic:)"
    )

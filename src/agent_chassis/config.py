from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ChassisSettings(BaseSettings):
    """Environment-injected chassis configuration (deployment contract).

    Manifests name abstract model tiers; the mapping to concrete models is an
    environment concern so roles stay portable across providers and model
    generations.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Tier values are pydantic-ai model strings (provider-prefixed), e.g.
    # "openai:gpt-5.4-mini-2026-03-17" or "anthropic:claude-opus-4-8".
    model_tier_light: str = "openai:gpt-4o-mini"
    model_tier_standard: str = "openai:gpt-5.4-mini-2026-03-17"
    # No default on purpose: a manifest asking for frontier must get a
    # deliberately chosen model, never a silent substitute (fail loud at
    # role build). Set MODEL_TIER_FRONTIER per deployment.
    model_tier_frontier: str | None = None

    # Durable execution (injected by the deployment in a cluster; local values for dev).
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "agent-chassis"
    # D9: the worker fails closed without this — no plaintext mode exists.
    temporal_payload_codec_key: str | None = None
    temporal_payload_codec_key_id: str = "default"

    manifest_path: str | None = None
    packs_dir: str | None = None

    # OTLP/Phoenix trace collector; unset disables tracing (fail-soft).
    phoenix_collector_endpoint: str | None = None

    # Memory substrate (D8); unset runs the role memory-less.
    postgres_url: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    conversation_idle_seconds: int = 3600
    # Passthrough correlation label (D16) — isolation is the deployment environment's job.
    tenant: str = "local"
    # Push target for the Slack-shaped channel; unset disables the sender.
    slack_webhook_url: str | None = None
    # Slack inbound HMAC verification (D21); unset allows unsigned webhooks
    # with a loud warning (local dev only).
    slack_signing_secret: str | None = None
    # Channel auth (D21): comma-separated "token:principal" pairs. When set,
    # every /v1/* endpoint requires a Bearer token and the approver identity
    # in the audit log is the token's principal, never the request body.
    # When unset, /v1/approvals and /v1/tasks refuse to serve.
    http_bearer_tokens: str | None = None

    def principal_for_token(self, token: str) -> str | None:
        """Resolve a bearer token to its audited principal (D21).

        Args:
            token: The raw bearer token from the ``Authorization`` header.

        Returns:
            The principal the token maps to (``"unnamed"`` if the pair omitted
            one), or ``None`` if auth is unconfigured or the token is unknown.
        """
        if not self.http_bearer_tokens:
            return None
        for pair in self.http_bearer_tokens.split(","):
            candidate, _, principal = pair.strip().partition(":")
            if candidate and candidate == token:
                return principal or "unnamed"
        return None

    def model_for_tier(self, tier: str) -> str:
        """Resolve an abstract tier to its configured concrete model string.

        Args:
            tier: One of ``light`` | ``standard`` | ``frontier``.

        Returns:
            The provider-prefixed model string for that tier.

        Raises:
            ValueError: If ``tier`` is unknown, or if it is configured to an
                empty value (notably ``frontier``, which has no default and
                must be set per deployment — never silently substituted).
        """
        try:
            value = {
                "light": self.model_tier_light,
                "standard": self.model_tier_standard,
                "frontier": self.model_tier_frontier,
            }[tier]
        except KeyError:
            raise ValueError(
                f"unknown model tier {tier!r} (expected light|standard|frontier)"
            ) from None
        if not value:
            raise ValueError(
                f"manifest requests model tier {tier!r} but MODEL_TIER_"
                f"{tier.upper()} is not configured — refusing to silently"
                " substitute another tier"
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> ChassisSettings:
    """Cached settings singleton."""
    return ChassisSettings()

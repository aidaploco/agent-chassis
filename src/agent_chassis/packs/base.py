from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from agent_chassis.contracts import MessageEnvelope


@dataclass(frozen=True)
class ToolSpec:
    """A vendor-free tool definition.

    ``handler`` is a plain async callable with typed parameters; the runtime
    adapter converts it to a vendor tool (schema derived from the signature).
    ``pure=True`` declares the tool does no I/O, letting the adapter run it
    in-workflow instead of as a Temporal activity (D1 per-tool opt-out) —
    pure handlers must therefore also be deterministic.
    """

    name: str
    handler: Callable[..., Awaitable[Any]]
    description: str
    pure: bool = False


class PackConfig(BaseModel):
    """Base class for pack config models.

    Bounds ("tunable within schema-validated bounds", D5/D7) are expressed as
    field constraints (``ge``/``le``/enums) on the subclass — the manifest can
    only override within them, and violations fail at resolve time.
    """

    model_config = ConfigDict(extra="forbid")


class CapabilityPack(ABC):
    """A reusable capability: tools + instruction fragment + config + evals.

    The eval suite travels with the pack (D17): every role composing this
    pack inherits its regression tests.
    """

    name: ClassVar[str]
    # One line of routing vocabulary: what requests belong to an agent
    # hosting this pack. Used when composing OTHER agents' routing blocks.
    description: ClassVar[str] = ""
    config_model: ClassVar[type[PackConfig]] = PackConfig
    required_secrets: ClassVar[tuple[str, ...]] = ()
    required_scopes: ClassVar[tuple[str, ...]] = ()

    @abstractmethod
    def instructions(self, config: PackConfig) -> str:
        """Instruction fragment contributed to the hosting agent's prompt."""

    @abstractmethod
    def tools(self, config: PackConfig) -> list[ToolSpec]:
        """Tools contributed to the hosting agent."""

    def eval_cases_dir(self) -> Path | None:
        """Directory of bundled eval case YAMLs, if the pack ships any."""
        return None


@dataclass(frozen=True)
class GuardrailVerdict:
    passed: bool
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class GuardrailPack(ABC):
    """An input guardrail, run by the chassis as a pre-flight activity (D5).

    Thresholds live in ``config_model`` with declared bounds; every manifest
    override is audit-logged (D11).
    """

    name: ClassVar[str]
    config_model: ClassVar[type[PackConfig]] = PackConfig

    @abstractmethod
    async def check(
        self,
        message: MessageEnvelope,
        config: PackConfig,
        context: Sequence[str] = (),
    ) -> GuardrailVerdict:
        """Return the verdict for one inbound message.

        ``context`` is a small chassis-owned window of recent conversation
        lines (oldest first). Guardrails MUST judge the message in that
        light — a bare email address is off-topic alone but on-topic as the
        answer to "what's your account email?". Packs that don't need
        context simply ignore it.
        """

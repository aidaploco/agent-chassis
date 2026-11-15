from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConversationHistory(BaseModel):
    """Conversation history, opaque to the chassis (D6a).

    ``entries`` are vendor-shaped and owned by the runtime adapter identified
    by ``runtime``; the chassis only carries them through workflow state and
    continue-as-new. Compaction is delegated to the adapter
    (``RuntimeAdapter.compact_history``), which may fold older entries into
    ``summary``.
    """

    model_config = ConfigDict(extra="forbid")

    runtime: str
    entries: list[Any] = Field(default_factory=list)
    summary: str | None = None


class HistoryBudget(BaseModel):
    """Compaction policy from the role manifest (``runtime.history_budget``).

    ``max_turns`` is how many user turns compaction keeps. The chassis
    compacts once history holds a quarter more than that, so history stays
    within 1.25x ``max_turns``. ``summary_max_tokens`` is reserved for
    model-written summaries and is not used by the V1 adapter.
    """

    model_config = ConfigDict(extra="forbid")

    max_turns: int = Field(default=40, ge=1)
    summary_max_tokens: int = Field(default=512, ge=1)

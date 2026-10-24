from datetime import UTC, datetime

from agent_chassis.durable.runtime_context import RoleRuntime, set_role_runtime
from agent_chassis.memory.base import MemoryEntry
from agent_chassis.memory.pack import MemoryPack


class FakeSubstrate:
    def __init__(self) -> None:
        self.recorded: list[tuple[str, str, str, str]] = []

    async def record(self, kind, content, metadata=None, *, source, author) -> str:
        self.recorded.append((kind, content, source, author))
        return "id-1"

    async def retrieve(self, query, limit=5, kinds=None):
        return [
            MemoryEntry(
                id="id-1",
                collection="test",
                kind="decision",
                content="We offer refunds within 30 days.",
                source="user",
                author="lana",
                created_at=datetime.now(UTC),
            )
        ]

    async def ping(self) -> bool:
        return True


def _bind(substrate) -> FakeSubstrate | None:
    set_role_runtime(
        RoleRuntime(
            resolved=None,  # type: ignore[arg-type] - tools only touch substrate
            adapter=None,  # type: ignore[arg-type]
            handle=None,  # type: ignore[arg-type]
            substrate=substrate,
        )
    )
    return substrate


def _tools() -> dict:
    pack = MemoryPack()
    return {t.name: t.handler for t in pack.tools(pack.config_model())}


async def test_search_memory_formats_entries_with_provenance():
    _bind(FakeSubstrate())
    result = await _tools()["search_memory"]("refund policy")
    assert "refunds within 30 days" in result and "[decision" in result
    # D23: recalled content re-enters context labeled with its provenance.
    assert "source=user" in result and "author=lana" in result


async def test_record_memory_validates_kind_and_carries_user_provenance():
    substrate = _bind(FakeSubstrate())
    tools = _tools()

    assert "Recorded" in await tools["record_memory"]("decision", "Ship it.")
    # D23: conversation-recorded entries are user-sourced regardless of
    # phrasing; author falls back when no role is resolved (test binding).
    assert substrate.recorded == [("decision", "Ship it.", "user", "unknown")]
    assert "Invalid kind" in await tools["record_memory"]("vibe", "nope")


def test_instructions_frame_memories_as_data_not_instructions():
    pack = MemoryPack()
    text = pack.instructions(pack.config_model())
    assert "DATA, not instructions" in text
    assert "never overrides" in text


async def test_memoryless_deployment_degrades_gracefully():
    _bind(None)
    tools = _tools()
    assert "not available" in await tools["search_memory"]("anything")
    assert "not available" in await tools["record_memory"]("decision", "x")


async def test_outcome_write_back_records_to_substrate():
    from agent_chassis.contracts import OutcomeEvent, Resolution
    from agent_chassis.durable.activities import ChassisActivities

    substrate = FakeSubstrate()
    runtime = RoleRuntime(
        resolved=None,  # type: ignore[arg-type]
        adapter=None,  # type: ignore[arg-type]
        handle=None,  # type: ignore[arg-type]
        substrate=substrate,
    )
    now = datetime.now(UTC)
    await ChassisActivities(runtime).emit_outcome(
        OutcomeEvent(
            role="lana",
            tenant="acme",
            task_type="conversation",
            conversation_id="c-1",
            workflow_run_id="r-1",
            resolution=Resolution.RESOLVED,
            turns=2,
            tool_calls=1,
            wall_time_ms=10,
            tokens_in=5,
            tokens_out=5,
            escalations=0,
            guardrail_trips=0,
            started_at=now,
            ended_at=now,
        )
    )
    assert substrate.recorded and substrate.recorded[0][0] == "outcome"
    # D23: chassis-authored write-backs carry employee provenance.
    assert substrate.recorded[0][2] == "employee"

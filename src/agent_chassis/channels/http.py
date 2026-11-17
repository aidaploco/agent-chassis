"""HTTP chat channel — sync: the reply rides the update result, the same
request/response shape a plain chat endpoint offers."""

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from opentelemetry import trace
from pydantic import BaseModel, ConfigDict
from temporalio.client import Client, WithStartWorkflowOperation
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.contrib.opentelemetry import OpenTelemetryInterceptor
from temporalio.exceptions import TemporalError
from temporalio.service import RPCError, RPCStatusCode

from agent_chassis.channels.records import (
    AuditReader,
    OutcomeReader,
    register_record_routes,
)
from agent_chassis.config import ChassisSettings, get_settings
from agent_chassis.contracts import ContentPart, MessageEnvelope
from agent_chassis.durable.converter import build_data_converter
from agent_chassis.durable.models import (
    ApprovalSignal,
    ConversationParams,
    TaskParams,
    TaskReply,
    TaskStatus,
    TurnReply,
)
from agent_chassis.durable.workflows import ConversationWorkflow, TaskWorkflow
from agent_chassis.introspection import SelfDescription, build_self_description
from agent_chassis.memory.base import MemorySubstrate
from agent_chassis.packs.registry import ResolvedRole
from agent_chassis.utils.logger import get_logger

CHANNEL = "http"
_logger = get_logger(__name__)


def _bearer_principal(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return get_settings().principal_for_token(authorization.removeprefix("Bearer "))


async def require_auth(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """D21 fail-closed dependency: valid token or nothing.

    Endpoints whose effects carry identity (approvals) or reach gated tools
    (tasks) refuse to serve at all until HTTP_BEARER_TOKENS is configured —
    an unauthenticated approval would put a caller-invented name in the
    audit log.
    """
    if not get_settings().http_bearer_tokens:
        raise HTTPException(
            status_code=503,
            detail="endpoint disabled: HTTP_BEARER_TOKENS is not configured",
        )
    principal = _bearer_principal(authorization)
    if principal is None:
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")
    return principal


async def optional_auth(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """D21 dev-friendly dependency: enforced once tokens are configured;
    anonymous (with the posture logged at startup) when they are not."""
    if not get_settings().http_bearer_tokens:
        return "anonymous"
    principal = _bearer_principal(authorization)
    if principal is None:
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")
    return principal


class ChatRequest(BaseModel):
    """``sender`` is honoured only in the anonymous local-dev posture. Once
    HTTP_BEARER_TOKENS is configured the sender is the authenticated
    principal (D21) and this field is ignored."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    message: str
    sender: str = "user"


class TaskStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str
    prompt_override: str | None = None


class TaskStartResponse(BaseModel):
    workflow_id: str
    task: str


class TaskSummary(BaseModel):
    """One row of ``GET /v1/tasks``: enough to find a task and see whether it is
    waiting for a person. ``GET /v1/tasks/{id}`` has the detail (pending calls)."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    # Which manifest task this run is, when its id says so (API-started and
    # schedule-started runs both do).
    task: str | None
    # Temporal's own status: RUNNING, COMPLETED, FAILED, TERMINATED, ...
    status: str
    # For a RUNNING task: "running" or "waiting_on_human". None if it could not be
    # asked (the worker is down) or the run has finished.
    state: str | None
    pending_approvals: int
    started_at: datetime | None
    closed_at: datetime | None


# What a visibility-query literal may contain. The queue name comes from settings,
# never from a caller, but a value that could break out of the quotes is refused
# rather than escaped.
_SAFE_QUERY_LITERAL = re.compile(r"^[A-Za-z0-9._:/-]+$")
_MAX_LISTED_TASKS = 50
_STATUS_QUERY_TIMEOUT = 2.0


def _task_name(workflow_id: str, employee: str, names: set[str]) -> str | None:
    """Which manifest task a workflow id belongs to. Runs started through the API are
    ``task-<task>-<uuid>``; Temporal Schedules start ``task-<employee>-<task>-<time>``."""
    for name in names:
        if workflow_id.startswith((f"task-{name}-", f"task-{employee}-{name}-")):
            return name
    return None


class ApprovalRequest(BaseModel):
    """The approver is NOT a request field (D21): the audited identity is
    the authenticated principal, never caller-supplied text."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    approved: bool
    comment: str = ""
    call_id: str | None = None  # None decides every pending call


class ChatResponse(BaseModel):
    conversation_id: str
    content: str
    agent: str
    turn_number: int
    escalated: bool
    guardrail_tripped: str | None
    tools_called: list[str] = []


def _not_found(workflow_id: str) -> HTTPException:
    return HTTPException(
        status_code=404, detail=f"no task workflow with id {workflow_id!r}"
    )


def _not_found_as_404(exc: RPCError, workflow_id: str) -> HTTPException | None:
    """An unknown workflow id is the caller's error (404), not a server
    fault (500). Anything else is left to propagate."""
    if exc.status == RPCStatusCode.NOT_FOUND:
        return HTTPException(
            status_code=404, detail=f"no task workflow with id {workflow_id!r}"
        )
    return None


def workflow_id_for(conversation_id: str) -> str:
    return f"conversation-{conversation_id}"


async def send_to_conversation(
    client: Client,
    settings: ChassisSettings,
    envelope: MessageEnvelope,
    tenant: str,
) -> TurnReply:
    """Update-with-start: first message starts the workflow, later ones
    attach to the running one (or restart after idle completion)."""
    start_op = WithStartWorkflowOperation(
        ConversationWorkflow.run,
        ConversationParams(
            conversation_id=envelope.conversation_id,
            tenant=tenant,
            channel=envelope.channel,
            idle_timeout_seconds=settings.conversation_idle_seconds,
        ),
        id=workflow_id_for(envelope.conversation_id),
        task_queue=settings.temporal_task_queue,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    return await client.execute_update_with_start_workflow(
        ConversationWorkflow.send_message,
        envelope,
        start_workflow_operation=start_op,
    )


def create_app(
    get_client: Callable[[], Awaitable[Client]],
    resolved: ResolvedRole | None = None,
    get_substrate: Callable[[], Awaitable[MemorySubstrate | None]] | None = None,
    get_audit: Callable[[], Awaitable[AuditReader | None]] | None = None,
    get_outcomes: Callable[[], Awaitable[OutcomeReader | None]] | None = None,
) -> FastAPI:
    """App factory; the client provider is injected so tests can supply a
    dev-server client while deployments connect from settings."""
    app = FastAPI(title="AI Employee — HTTP channel")
    tracer = trace.get_tracer("agent_chassis.channels.http")
    if not get_settings().http_bearer_tokens:
        _logger.warning(
            "HTTP_BEARER_TOKENS not configured — /v1/messages and /v1/self"
            " serve anonymously (local dev posture); /v1/tasks and"
            " /v1/approvals are DISABLED (D21)"
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if resolved is not None:

        @app.get("/v1/self", response_model=SelfDescription)
        async def describe_self(
            principal: Annotated[str, Depends(optional_auth)],
        ) -> SelfDescription:
            """The Employee's self-description: manifest view (no secret
            values exist in manifests), capabilities, recent outcomes."""
            substrate = await get_substrate() if get_substrate else None
            return await build_self_description(resolved, substrate)

        register_record_routes(
            app,
            employee=resolved.manifest.employee.name,
            get_audit=get_audit,
            get_outcomes=get_outcomes,
            auth=require_auth,
        )

    @app.post("/v1/messages", response_model=ChatResponse)
    async def post_message(
        request: ChatRequest,
        principal: Annotated[str, Depends(optional_auth)],
    ) -> ChatResponse:
        settings = get_settings()
        client = await get_client()
        # D21: identity is never caller-supplied. With auth configured the
        # sender is the token's principal; the body field only names the
        # sender when the endpoint is serving anonymously.
        sender = principal if settings.http_bearer_tokens else request.sender
        envelope = MessageEnvelope(
            sender=sender,
            tenant=settings.tenant,
            conversation_id=request.conversation_id,
            channel=CHANNEL,
            parts=[ContentPart(text=request.message)],
        )
        # Root span of the turn's trace. The openinference.* / session.id /
        # input-output attributes are what Phoenix renders: kind=CHAIN gives
        # it a proper icon instead of "unknown", session.id groups all turns
        # of one conversation in the Sessions view, and input/output.value
        # fill the I/O pane.
        with tracer.start_as_current_span(
            "conversation.turn",
            attributes={
                "openinference.span.kind": "CHAIN",
                "session.id": request.conversation_id,
                "user.id": sender,
                "input.value": request.message,
                "channel": CHANNEL,
            },
        ) as span:
            reply = await send_to_conversation(
                client, settings, envelope, tenant=settings.tenant
            )
            span.set_attribute("output.value", reply.content)
            span.set_attribute("agent.name", reply.agent)
            span.set_attribute("turn.number", reply.turn_number)
            if reply.guardrail_tripped:
                span.set_attribute("guardrail.tripped", reply.guardrail_tripped)
        return ChatResponse(
            conversation_id=request.conversation_id,
            content=reply.content,
            agent=reply.agent,
            turn_number=reply.turn_number,
            escalated=reply.escalated,
            guardrail_tripped=reply.guardrail_tripped,
            tools_called=[e.name for e in reply.events if e.kind == "tool_call"],
        )

    @app.post("/v1/tasks", response_model=TaskStartResponse)
    async def start_task(
        request: TaskStartRequest,
        principal: Annotated[str, Depends(require_auth)],
    ) -> TaskStartResponse:
        """Kick off a manifest task (D6). Returns immediately — tasks may
        wait days on approval; poll GET /v1/tasks/{workflow_id}."""
        settings = get_settings()
        if resolved is not None and request.task not in {
            t.name for t in resolved.manifest.tasks
        }:
            # Reject at submission — otherwise the caller gets a 200 plus a
            # workflow that fails on its first task, discoverable only later.
            raise HTTPException(
                status_code=404,
                detail=f"unknown task {request.task!r}; this role declares:"
                f" {sorted(t.name for t in resolved.manifest.tasks)}",
            )
        client = await get_client()
        workflow_id = f"task-{request.task}-{uuid.uuid4()}"
        await client.start_workflow(
            TaskWorkflow.run,
            TaskParams(
                task=request.task,
                tenant=settings.tenant,
                prompt_override=request.prompt_override,
            ),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        return TaskStartResponse(workflow_id=workflow_id, task=request.task)

    async def own_task_handle(workflow_id: str):
        """A handle to one of THIS Employee's tasks. Employees share a Temporal namespace, so
        a workflow id alone proves nothing: a task on another Employee's queue is answered
        exactly like an unknown id (404), so neither its state nor its approvals can be reached
        through this unit, and nothing is revealed about whether it exists."""
        client = await get_client()
        handle = client.get_workflow_handle_for(TaskWorkflow.run, workflow_id)
        try:
            description = await handle.describe()
        except RPCError as exc:
            if not_found := _not_found_as_404(exc, workflow_id):
                raise not_found from exc
            raise
        if description.task_queue != get_settings().temporal_task_queue:
            raise _not_found(workflow_id)
        return handle, description

    @app.get("/v1/tasks", response_model=list[TaskSummary])
    async def list_tasks(
        principal: Annotated[str, Depends(require_auth)],
        limit: Annotated[int, Query(ge=1, le=_MAX_LISTED_TASKS)] = 20,
    ) -> list[TaskSummary]:
        """Recent task runs of THIS Employee, newest first, including ones nobody
        started through the API (a schedule's). Scoped by this unit's task queue, so
        Employees sharing a Temporal namespace do not see each other's tasks."""
        settings = get_settings()
        queue = settings.temporal_task_queue
        if not _SAFE_QUERY_LITERAL.match(queue):
            raise HTTPException(
                status_code=500, detail="TEMPORAL_TASK_QUEUE has unsafe characters"
            )
        employee = resolved.manifest.employee.name if resolved else ""
        names = {t.name for t in resolved.manifest.tasks} if resolved else set()
        client = await get_client()
        found = [
            w
            async for w in client.list_workflows(
                f"WorkflowType = 'TaskWorkflow' AND TaskQueue = '{queue}'",
                # Not every visibility store supports ORDER BY (the dev server's does not),
                # and the default lists open runs first. Fetch extra, sort here, trim below.
                limit=min(limit * 2, 2 * _MAX_LISTED_TASKS),
            )
        ]

        async def summarize(w) -> TaskSummary:
            state: str | None = None
            pending = 0
            if w.status and w.status.name == "RUNNING":
                try:  # per row, soft: one unreachable worker must not fail the list
                    live = await asyncio.wait_for(
                        client.get_workflow_handle_for(TaskWorkflow.run, w.id).query(
                            TaskWorkflow.status
                        ),
                        _STATUS_QUERY_TIMEOUT,
                    )
                    state, pending = live.state, len(live.pending)
                except Exception:  # noqa: BLE001
                    state = None
            return TaskSummary(
                workflow_id=w.id,
                task=_task_name(w.id, employee, names),
                status=w.status.name if w.status else "UNKNOWN",
                state=state,
                pending_approvals=pending,
                started_at=w.start_time,
                closed_at=w.close_time,
            )

        rows = list(await asyncio.gather(*(summarize(w) for w in found)))
        # Newest first, whatever order Temporal returned them in.
        rows.sort(
            key=lambda r: r.started_at or datetime.min.replace(tzinfo=UTC), reverse=True
        )
        return rows[:limit]

    @app.get("/v1/tasks/{workflow_id}", response_model=TaskStatus | TaskReply)
    async def task_status(
        workflow_id: str,
        principal: Annotated[str, Depends(require_auth)],
    ) -> TaskStatus | TaskReply:
        """Live status (incl. pending approvals) while running; the final
        TaskReply once done."""
        handle, description = await own_task_handle(workflow_id)
        if description.status and description.status.name != "RUNNING":
            try:
                return await handle.result()
            except TemporalError as exc:
                # A failed/terminated task is a reportable state, not a 500.
                cause = getattr(exc, "cause", None)
                return TaskStatus(task="", state="failed", error=str(cause or exc))
        return await handle.query(TaskWorkflow.status)

    @app.post("/v1/approvals")
    async def submit_approval(
        request: ApprovalRequest,
        principal: Annotated[str, Depends(require_auth)],
    ) -> dict[str, str]:
        """Approver decision (D10 signal); the audited identity (D11) is the
        authenticated principal — the body cannot name someone else."""
        handle, _ = await own_task_handle(request.workflow_id)
        try:
            await handle.signal(
                TaskWorkflow.approve,
                ApprovalSignal(
                    call_id=request.call_id,
                    approved=request.approved,
                    approver=principal,
                    comment=request.comment,
                ),
            )
        except RPCError as exc:
            if not_found := _not_found_as_404(exc, request.workflow_id):
                raise not_found from exc
            raise
        return {"status": "delivered", "approver": principal}

    return app


def default_client_provider() -> Callable[[], Awaitable[Client]]:  # pragma: no cover
    """Lazy env-configured Temporal client for deployment entrypoints."""
    lock = asyncio.Lock()
    cache: list[Client] = []

    async def get_client() -> Client:
        if not cache:
            async with lock:
                if not cache:
                    settings = get_settings()
                    cache.append(
                        await Client.connect(
                            settings.temporal_address,
                            namespace=settings.temporal_namespace,
                            data_converter=build_data_converter(settings),
                            # Propagates trace context into the workflow and
                            # its activities WITHOUT emitting the Start*/Run*
                            # plumbing spans the legacy TracingInterceptor
                            # always created (add_temporal_spans stays False).
                            interceptors=[OpenTelemetryInterceptor()],
                        )
                    )
        return cache[0]

    return get_client

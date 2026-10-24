"""HTTP chat channel — sync: the reply rides the update result, the same
request/response shape a plain chat endpoint offers."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from opentelemetry import trace
from temporalio.exceptions import TemporalError
from pydantic import BaseModel, ConfigDict
from temporalio.client import Client, WithStartWorkflowOperation
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.contrib.opentelemetry import OpenTelemetryInterceptor

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
    resolved: "ResolvedRole | None" = None,
    get_substrate: Callable[[], Awaitable[MemorySubstrate | None]] | None = None,
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

    @app.post("/v1/messages", response_model=ChatResponse)
    async def post_message(
        request: ChatRequest,
        principal: Annotated[str, Depends(optional_auth)],
    ) -> ChatResponse:
        settings = get_settings()
        client = await get_client()
        envelope = MessageEnvelope(
            sender=request.sender,
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
                "user.id": request.sender,
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

    @app.get("/v1/tasks/{workflow_id}", response_model=TaskStatus | TaskReply)
    async def task_status(
        workflow_id: str,
        principal: Annotated[str, Depends(require_auth)],
    ) -> TaskStatus | TaskReply:
        """Live status (incl. pending approvals) while running; the final
        TaskReply once done."""
        client = await get_client()
        handle = client.get_workflow_handle_for(TaskWorkflow.run, workflow_id)
        description = await handle.describe()
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
        client = await get_client()
        handle = client.get_workflow_handle_for(TaskWorkflow.run, request.workflow_id)
        await handle.signal(
            TaskWorkflow.approve,
            ApprovalSignal(
                call_id=request.call_id,
                approved=request.approved,
                approver=principal,
                comment=request.comment,
            ),
        )
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

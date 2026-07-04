"""Router for MCP interface providing Model Context Protocol endpoints."""

import functools
import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union, cast
from uuid import uuid4

from fastmcp import Context, FastMCP
from fastmcp.server.http import (
    StarletteWithLifespan,
)
from fastmcp.tools.tool import ToolResult

from agno.db.base import AsyncBaseDb, BaseDb, SessionType
from agno.db.schemas import UserMemory
from agno.os.mcp_results import build_run_tool_result
from agno.os.routers.memory.schemas import (
    UserMemorySchema,
)
from agno.os.schema import (
    AgentSessionDetailSchema,
    AgentSummaryResponse,
    ConfigResponse,
    InterfaceResponse,
    RunSchema,
    SessionSchema,
    TeamRunSchema,
    TeamSessionDetailSchema,
    TeamSummaryResponse,
    WorkflowRunSchema,
    WorkflowSessionDetailSchema,
    WorkflowSummaryResponse,
)
from agno.os.utils import (
    get_agent_by_id,
    get_db,
    get_team_by_id,
    get_workflow_by_id,
)
from agno.remote.base import RemoteDb
from agno.run.agent import RunEvent, RunOutput
from agno.run.team import TeamRunEvent, TeamRunOutput
from agno.run.workflow import WorkflowRunEvent, WorkflowRunOutput
from agno.session import AgentSession, TeamSession, WorkflowSession

if TYPE_CHECKING:
    from agno.os.app import AgentOS
    from agno.os.config import MCPServerConfig

logger = logging.getLogger(__name__)

# Built-in MCP tools are tagged by domain so they can be scoped as a group. The canonical
# tag set lives in agno/os/config.py next to the MCPServerConfig fields that consume it --
# single source of truth so adding a new tag is a one-place change.
from agno.os.config import MCP_BUILTIN_TAGS as _BUILTIN_TOOL_TAGS  # noqa: E402


def _enabled_builtin_tags(config: "Optional[MCPServerConfig]") -> set:
    """Resolve which built-in tool tags should be registered, given the MCP config.

    Returns the full set of built-in tags when no config is provided, preserving the
    default behavior (all built-in tools registered).
    """
    if config is None:
        return set(_BUILTIN_TOOL_TAGS)
    if not config.enable_builtin_tools:
        return set()
    enabled = set(config.include_tags) if config.include_tags else set(_BUILTIN_TOOL_TAGS)
    if config.exclude_tags:
        enabled -= set(config.exclude_tags)
    return enabled


def _builtin_tool_registrar(mcp: FastMCP, config: "Optional[MCPServerConfig]"):
    """Return a drop-in replacement for ``mcp.tool`` that scopes the built-in tools.

    When a tool's tags are enabled by the config, the tool is registered as usual.
    Otherwise the decorator is a no-op (the function is returned unregistered), so
    scoping happens at registration time without depending on FastMCP tool-removal APIs.
    """
    enabled_tags = _enabled_builtin_tags(config)

    def register(*args: Any, **kwargs: Any):
        tags = kwargs.get("tags") or set()
        if tags & enabled_tags:
            return mcp.tool(*args, **kwargs)

        def _skip(fn: Any) -> Any:
            return fn

        return _skip

    return register


def _register_custom_tools(mcp: FastMCP, config: "Optional[MCPServerConfig]") -> None:
    """Register any user-provided custom tools on the MCP server."""
    if config is None or not config.tools:
        return
    for tool in config.tools:
        _register_custom_tool(mcp, tool)


def _register_custom_tool(mcp: FastMCP, tool: Any) -> None:
    """Register a single custom tool, supporting plain callables and Agno tools/Functions."""
    from fastmcp.tools import Tool

    # Agno tool / Function: a callable ``entrypoint`` plus name/description metadata.
    entrypoint = getattr(tool, "entrypoint", None)
    if callable(entrypoint):
        name = getattr(tool, "name", None) or getattr(entrypoint, "__name__", None)
        description = getattr(tool, "description", None)
        mcp.add_tool(Tool.from_function(_inject_user_id(entrypoint), name=name, description=description))
        return

    # Plain callable: name/description inferred from ``__name__``/docstring.
    if callable(tool):
        mcp.add_tool(Tool.from_function(_inject_user_id(tool)))
        return

    raise TypeError(
        f"Cannot register MCP tool of type {type(tool).__name__!r}; expected a callable or an Agno tool/Function."
    )


def _inject_user_id(fn: Callable) -> Callable:
    """Inject the authenticated caller's user_id into a custom tool, hidden from clients.

    If ``fn`` declares a ``user_id`` parameter, return a wrapper that fills it with the
    resolved JWT subject at call time and drops it from the wrapper's signature -- so it
    does not appear in the MCP tool schema and cannot be supplied (or spoofed) by callers.
    Tools that do not declare ``user_id`` are returned unchanged.
    """
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return fn
    if "user_id" not in sig.parameters:
        return fn

    visible_params = [p for name, p in sig.parameters.items() if name != "user_id"]
    new_sig = sig.replace(parameters=visible_params)

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            kwargs["user_id"] = _resolve_user_id(None)
            return await fn(*args, **kwargs)

        async_wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        kwargs["user_id"] = _resolve_user_id(None)
        return fn(*args, **kwargs)

    wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
    return wrapper


def _resolve_user_id(caller_user_id: Optional[str]) -> Optional[str]:
    """Bind user_id to the JWT subject when an authenticated request is in flight."""
    from fastmcp.server.dependencies import get_http_request

    try:
        request = get_http_request()
    except RuntimeError:
        return caller_user_id

    state_user_id = getattr(getattr(request, "state", None), "user_id", None)
    if state_user_id is not None:
        return state_user_id
    return caller_user_id


# Events forwarded to the client as progress notifications during agent/team runs.
# Content deltas are deliberately excluded: MCP progress is a status channel, and
# per-token notifications would flood clients that request a progress token.
_TOOL_CALL_PROGRESS_EVENTS = frozenset(
    {
        RunEvent.tool_call_started.value,
        RunEvent.tool_call_completed.value,
        TeamRunEvent.tool_call_started.value,
        TeamRunEvent.tool_call_completed.value,
    }
)

# Error events captured so a failed run surfaces its real error message. The streaming
# error paths yield only these events -- the final run output is never yielded on failure.
_RUN_ERROR_EVENTS = frozenset({RunEvent.run_error.value, TeamRunEvent.run_error.value})


async def _report_progress(ctx: Context, progress: float, message: str, total: Optional[float] = None) -> None:
    """Send a progress notification; a failure here must never break the run.

    FastMCP no-ops when the client did not send a progressToken, so this is safe to
    call unconditionally.
    """
    try:
        await ctx.report_progress(progress=progress, total=total, message=message)
    except Exception:
        logger.debug("Failed to send MCP progress notification", exc_info=True)


def _describe_tool_call_event(event: Any) -> str:
    tool = getattr(event, "tool", None)
    tool_name = getattr(tool, "tool_name", None) or "tool"
    verb = "started" if str(getattr(event, "event", "")).endswith("Started") else "completed"
    return f"Tool call {verb}: {tool_name}"


async def _consume_agentic_stream(ctx: Context, stream: Any, label: str) -> Union[RunOutput, TeamRunOutput]:
    """Drive a streaming agent/team run and return its final output.

    The stream must be created with ``stream=True, stream_events=True,
    yield_run_output=True`` so tool-call events can be forwarded as progress
    notifications and the final ``RunOutput`` / ``TeamRunOutput`` arrives as the
    last yielded item. On failure the stream yields only a run-error event -- its
    message is captured so the client sees the real error, not a generic one.
    """
    final: Optional[Union[RunOutput, TeamRunOutput]] = None
    error_message: Optional[str] = None
    ticks = 0
    await _report_progress(ctx, 0.0, f"{label} started")
    async for item in stream:
        if isinstance(item, (RunOutput, TeamRunOutput)):
            final = item
            continue
        event = getattr(item, "event", None)
        if event in _TOOL_CALL_PROGRESS_EVENTS:
            ticks += 1
            await _report_progress(ctx, float(ticks), _describe_tool_call_event(item))
        elif event in _RUN_ERROR_EVENTS:
            error_message = getattr(item, "content", None) or "Run failed"
    if final is None:
        raise Exception(
            str(error_message) if error_message else f"{label} finished without producing a final run output"
        )
    return final


async def _run_agentic_component(
    ctx: Context, component: Any, message: str, user_id: Optional[str], session_id: Optional[str], label: str
) -> Union[RunOutput, TeamRunOutput]:
    """Shared run path for agents and teams: stream with progress, or plain await for remotes.

    Remote components proxy to another AgentOS over HTTP and their streaming ``arun``
    never yields the final output object, so they take the non-streaming path (no
    intermediate progress, same result contract).
    """
    from agno.agent.remote import RemoteAgent
    from agno.team.remote import RemoteTeam

    if isinstance(component, (RemoteAgent, RemoteTeam)):
        return await component.arun(message, user_id=user_id, session_id=session_id)

    stream = component.arun(
        message,
        user_id=user_id,
        session_id=session_id,
        stream=True,
        stream_events=True,
        yield_run_output=True,
    )
    return await _consume_agentic_stream(ctx, stream, label=label)


def _describe_step_event(event: Any, total_steps: Optional[float]) -> str:
    verb = "started" if str(getattr(event, "event", "")).endswith("Started") else "completed"
    step_name = getattr(event, "step_name", None) or "step"
    step_index = getattr(event, "step_index", None)
    if isinstance(step_index, tuple) and step_index and isinstance(step_index[0], int):
        step_index = step_index[0]
    if isinstance(step_index, int) and total_steps:
        return f"Step {verb}: {step_name} ({step_index + 1}/{int(total_steps)})"
    return f"Step {verb}: {step_name}"


async def _consume_workflow_stream(
    ctx: Context,
    workflow: Any,
    stream: Any,
    total_steps: Optional[float],
    user_id: Optional[str],
) -> WorkflowRunOutput:
    """Drive a streaming workflow run and return its final output.

    Workflow streams do not support ``yield_run_output``. Completed runs carry the
    full ``WorkflowRunOutput`` on the terminal event; paused / cancelled / step-error
    runs end the stream with NO workflow-level terminal event, so the persisted run
    is fetched back via ``workflow.aget_run_output`` -- the same source of truth the
    REST router uses. Events from nested workflows (``nested_depth > 0``) are skipped:
    terminal handling and progress apply to the outer run only, and a nested failure
    the outer workflow recovers from must not abort it.

    Progress values are a plain monotonic counter (the MCP spec requires each
    notification's progress to increase); the step k/n detail lives in the message.
    """
    from agno.run.workflow import BaseWorkflowRunOutputEvent

    final: Optional[WorkflowRunOutput] = None
    error_message: Optional[str] = None
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    ticks = 0.0
    await _report_progress(ctx, 0.0, "Workflow started")
    async for item in stream:
        if isinstance(item, WorkflowRunOutput):
            final = item
            continue
        if getattr(item, "nested_depth", 0):
            continue
        if isinstance(item, BaseWorkflowRunOutputEvent):
            run_id = getattr(item, "run_id", None) or run_id
            session_id = getattr(item, "session_id", None) or session_id
        event = getattr(item, "event", None)
        if event in (WorkflowRunEvent.step_started.value, WorkflowRunEvent.step_completed.value):
            ticks += 1.0
            await _report_progress(ctx, ticks, _describe_step_event(item, total_steps))
        elif event == WorkflowRunEvent.workflow_completed.value:
            final = getattr(item, "run_output", None) or final
        elif event == WorkflowRunEvent.workflow_error.value:
            # Do not raise mid-stream: closing the generator here would skip the
            # workflow's own error-status persistence. Capture and settle after.
            error_message = getattr(item, "error", None) or "Workflow run failed"
    if final is None and run_id is not None:
        try:
            final = await workflow.aget_run_output(run_id=run_id, session_id=session_id, user_id=user_id)
        except Exception:
            logger.debug("Could not fetch persisted workflow run %s after stream end", run_id, exc_info=True)
    if final is None:
        raise Exception(
            str(error_message) if error_message else "Workflow run finished without producing a final run output"
        )
    return final


def build_mcp_server(
    os: "AgentOS",
) -> FastMCP:
    """Build the FastMCP server for an AgentOS.

    Registers the built-in tools (scoped by ``os.mcp_config``) and any custom tools.
    Split out from :func:`get_mcp_server` so the tool surface can be exercised directly
    by an in-memory MCP client in tests, without the HTTP/JWT layer.
    """
    mcp_config: "Optional[MCPServerConfig]" = getattr(os, "mcp_config", None)

    # Create an MCP server
    mcp = FastMCP(os.name or "AgentOS")

    # Decorator used to register the built-in tools. Honors ``mcp_config`` scoping;
    # behaves exactly like ``mcp.tool`` when no config (or default config) is provided.
    register_builtin_tool = _builtin_tool_registrar(mcp, mcp_config)

    # How the run tools serialize their results ("trimmed" keeps the frontend model's
    # context clean; "full" is the escape hatch for programmatic clients).
    result_mode = mcp_config.result_mode if mcp_config is not None else "trimmed"

    @register_builtin_tool(
        name="get_agentos_config",
        description="Get the configuration of the AgentOS",
        tags={"core"},
        output_schema=ConfigResponse.model_json_schema(),
    )  # type: ignore
    async def config() -> ConfigResponse:
        return ConfigResponse(
            os_id=os.id or "AgentOS",
            description=os.description,
            available_models=os.config.available_models if os.config else [],
            databases=[db.id for db_list in os.dbs.values() for db in db_list],
            chat=os.config.chat if os.config else None,
            manifest=os.config.manifest if os.config else None,
            session=os._get_session_config(),
            memory=os._get_memory_config(),
            learning=os._get_learning_config(),
            knowledge=os._get_knowledge_config(),
            evals=os._get_evals_config(),
            metrics=os._get_metrics_config(),
            traces=os._get_traces_config(),
            agents=[AgentSummaryResponse.from_agent(a) for a in os.agents] if os.agents else [],
            teams=[TeamSummaryResponse.from_team(t) for t in os.teams] if os.teams else [],
            workflows=[WorkflowSummaryResponse.from_workflow(w) for w in os.workflows] if os.workflows else [],
            interfaces=[
                InterfaceResponse(type=interface.type, version=interface.version, route=interface.prefix)
                for interface in os.interfaces
            ],
        )

    # ==================== Core Run Tools ====================

    @register_builtin_tool(name="run_agent", description="Run an agent with a message", tags={"core"})  # type: ignore
    async def run_agent(
        agent_id: str,
        message: str,
        ctx: Context,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ToolResult:
        agent = get_agent_by_id(agent_id, os.agents)
        if agent is None:
            raise Exception(f"Agent {agent_id} not found")
        user_id = _resolve_user_id(user_id)
        run_output = await _run_agentic_component(
            ctx, agent, message, user_id, session_id, label=f"Agent {agent.name or agent_id}"
        )
        return build_run_tool_result(run_output, result_mode)

    @register_builtin_tool(name="run_team", description="Run a team with a message", tags={"core"})  # type: ignore
    async def run_team(
        team_id: str,
        message: str,
        ctx: Context,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ToolResult:
        team = get_team_by_id(team_id, os.teams)
        if team is None:
            raise Exception(f"Team {team_id} not found")
        user_id = _resolve_user_id(user_id)
        run_output = await _run_agentic_component(
            ctx, team, message, user_id, session_id, label=f"Team {team.name or team_id}"
        )
        return build_run_tool_result(run_output, result_mode)

    @register_builtin_tool(name="run_workflow", description="Run a workflow with a message", tags={"core"})  # type: ignore
    async def run_workflow(
        workflow_id: str,
        message: str,
        ctx: Context,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ToolResult:
        from agno.workflow.remote import RemoteWorkflow

        workflow = get_workflow_by_id(workflow_id, os.workflows)
        if workflow is None:
            raise Exception(f"Workflow {workflow_id} not found")
        user_id = _resolve_user_id(user_id)
        if isinstance(workflow, RemoteWorkflow):
            run_output = await workflow.arun(message, user_id=user_id, session_id=session_id)
            return build_run_tool_result(run_output, result_mode)
        steps = getattr(workflow, "steps", None)
        total_steps = float(len(steps)) if isinstance(steps, (list, tuple)) and steps else None
        stream = workflow.arun(
            message,
            user_id=user_id,
            session_id=session_id,
            stream=True,
            stream_events=True,
        )
        run_output = await _consume_workflow_stream(ctx, workflow, stream, total_steps, user_id)
        return build_run_tool_result(run_output, result_mode)

    # ==================== Session Management Tools ====================

    @register_builtin_tool(
        name="get_sessions",
        description="Get paginated list of sessions with optional filtering by type, component, user, and name",
        tags={"session"},
    )  # type: ignore
    async def get_sessions(
        db_id: str,
        session_type: str = "agent",
        component_id: Optional[str] = None,
        user_id: Optional[str] = None,
        session_name: Optional[str] = None,
        limit: int = 20,
        page: int = 1,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> Dict[str, Any]:
        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)
        session_type_enum = SessionType(session_type)
        if isinstance(db, RemoteDb):
            result = await db.get_sessions(
                session_type=session_type_enum,
                component_id=component_id,
                user_id=user_id,
                session_name=session_name,
                limit=limit,
                page=page,
                sort_by=sort_by,
                sort_order=sort_order,
                db_id=db_id,
            )
            return result.model_dump()

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            sessions, total_count = await db.get_sessions(
                session_type=session_type_enum,
                component_id=component_id,
                user_id=user_id,
                session_name=session_name,
                limit=limit,
                page=page,
                sort_by=sort_by,
                sort_order=sort_order,
                deserialize=False,
            )
        else:
            sessions, total_count = db.get_sessions(
                session_type=session_type_enum,
                component_id=component_id,
                user_id=user_id,
                session_name=session_name,
                limit=limit,
                page=page,
                sort_by=sort_by,
                sort_order=sort_order,
                deserialize=False,
            )

        return {
            "data": [SessionSchema.from_dict(session).model_dump() for session in sessions],  # type: ignore
            "meta": {
                "page": page,
                "limit": limit,
                "total_count": total_count,
                "total_pages": (total_count + limit - 1) // limit if limit > 0 else 0,  # type: ignore
            },
        }

    @register_builtin_tool(
        name="get_session",
        description="Get detailed information about a specific session by ID",
        tags={"session"},
    )  # type: ignore
    async def get_session(
        session_id: str,
        db_id: str,
        session_type: str = "agent",
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)
        session_type_enum = SessionType(session_type)

        if isinstance(db, RemoteDb):
            result = await db.get_session(
                session_id=session_id,
                session_type=session_type_enum,
                user_id=user_id,
                db_id=db_id,
            )
            return result.model_dump()

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            session = await db.get_session(session_id=session_id, session_type=session_type_enum, user_id=user_id)
        else:
            db = cast(BaseDb, db)
            session = db.get_session(session_id=session_id, session_type=session_type_enum, user_id=user_id)

        if not session:
            raise Exception(f"Session {session_id} not found")

        if session_type_enum == SessionType.AGENT:
            return AgentSessionDetailSchema.from_session(session).model_dump()  # type: ignore
        elif session_type_enum == SessionType.TEAM:
            return TeamSessionDetailSchema.from_session(session).model_dump()  # type: ignore
        else:
            return WorkflowSessionDetailSchema.from_session(session).model_dump()  # type: ignore

    @register_builtin_tool(
        name="create_session",
        description="Create a new session for an agent, team, or workflow",
        tags={"session"},
    )  # type: ignore
    async def create_session(
        db_id: str,
        session_type: str = "agent",
        session_id: Optional[str] = None,
        session_name: Optional[str] = None,
        session_state: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        team_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        import time

        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)
        session_type_enum = SessionType(session_type)

        # Generate session_id if not provided
        session_id = session_id or str(uuid4())

        if isinstance(db, RemoteDb):
            result = await db.create_session(
                session_type=session_type_enum,
                session_id=session_id,
                session_name=session_name,
                session_state=session_state,
                metadata=metadata,
                user_id=user_id,
                agent_id=agent_id,
                team_id=team_id,
                workflow_id=workflow_id,
                db_id=db_id,
            )
            return result.model_dump()

        # Prepare session_data
        session_data: Dict[str, Any] = {}
        if session_state is not None:
            session_data["session_state"] = session_state
        if session_name is not None:
            session_data["session_name"] = session_name

        current_time = int(time.time())

        # Create the appropriate session type
        session: Union[AgentSession, TeamSession, WorkflowSession]
        if session_type_enum == SessionType.AGENT:
            session = AgentSession(
                session_id=session_id,
                agent_id=agent_id,
                user_id=user_id,
                session_data=session_data if session_data else None,
                metadata=metadata,
                created_at=current_time,
                updated_at=current_time,
            )
        elif session_type_enum == SessionType.TEAM:
            session = TeamSession(
                session_id=session_id,
                team_id=team_id,
                user_id=user_id,
                session_data=session_data if session_data else None,
                metadata=metadata,
                created_at=current_time,
                updated_at=current_time,
            )
        else:
            session = WorkflowSession(
                session_id=session_id,
                workflow_id=workflow_id,
                user_id=user_id,
                session_data=session_data if session_data else None,
                metadata=metadata,
                created_at=current_time,
                updated_at=current_time,
            )

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            created_session = await db.upsert_session(session, deserialize=True)
        else:
            created_session = db.upsert_session(session, deserialize=True)

        if not created_session:
            raise Exception("Failed to create session")

        if session_type_enum == SessionType.AGENT:
            return AgentSessionDetailSchema.from_session(created_session).model_dump()  # type: ignore
        elif session_type_enum == SessionType.TEAM:
            return TeamSessionDetailSchema.from_session(created_session).model_dump()  # type: ignore
        else:
            return WorkflowSessionDetailSchema.from_session(created_session).model_dump()  # type: ignore

    @register_builtin_tool(
        name="get_session_runs",
        description="Get all runs for a specific session",
        tags={"session"},
    )  # type: ignore
    async def get_session_runs(
        session_id: str,
        db_id: str,
        session_type: str = "agent",
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)
        session_type_enum = SessionType(session_type)

        if isinstance(db, RemoteDb):
            result = await db.get_session_runs(
                session_id=session_id,
                session_type=session_type_enum,
                user_id=user_id,
                db_id=db_id,
            )
            return [r.model_dump() for r in result]

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            session = await db.get_session(
                session_id=session_id, session_type=session_type_enum, user_id=user_id, deserialize=False
            )
        else:
            session = db.get_session(
                session_id=session_id, session_type=session_type_enum, user_id=user_id, deserialize=False
            )

        if not session:
            raise Exception(f"Session {session_id} not found")

        runs = session.get("runs")  # type: ignore
        if not runs:
            return []

        run_responses: List[Dict[str, Any]] = []
        for run in runs:
            if session_type_enum == SessionType.AGENT:
                run_responses.append(RunSchema.from_dict(run).model_dump())
            elif session_type_enum == SessionType.TEAM:
                if run.get("agent_id") is not None:
                    run_responses.append(RunSchema.from_dict(run).model_dump())
                else:
                    run_responses.append(TeamRunSchema.from_dict(run).model_dump())
            else:
                if run.get("workflow_id") is not None:
                    run_responses.append(WorkflowRunSchema.from_dict(run).model_dump())
                elif run.get("team_id") is not None:
                    run_responses.append(TeamRunSchema.from_dict(run).model_dump())
                else:
                    run_responses.append(RunSchema.from_dict(run).model_dump())

        return run_responses

    @register_builtin_tool(
        name="get_session_run",
        description="Get a specific run from a session",
        tags={"session"},
    )  # type: ignore
    async def get_session_run(
        session_id: str,
        run_id: str,
        db_id: str,
        session_type: str = "agent",
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)
        session_type_enum = SessionType(session_type)

        if isinstance(db, RemoteDb):
            result = await db.get_session_run(
                session_id=session_id,
                run_id=run_id,
                session_type=session_type_enum,
                user_id=user_id,
                db_id=db_id,
            )
            return result.model_dump()

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            session = await db.get_session(
                session_id=session_id, session_type=session_type_enum, user_id=user_id, deserialize=False
            )
        else:
            session = db.get_session(
                session_id=session_id, session_type=session_type_enum, user_id=user_id, deserialize=False
            )

        if not session:
            raise Exception(f"Session {session_id} not found")

        runs = session.get("runs")  # type: ignore
        if not runs:
            raise Exception(f"Session {session_id} has no runs")

        target_run = None
        for run in runs:
            if run.get("run_id") == run_id:
                target_run = run
                break

        if not target_run:
            raise Exception(f"Run {run_id} not found in session {session_id}")

        if target_run.get("workflow_id") is not None:
            return WorkflowRunSchema.from_dict(target_run).model_dump()
        elif target_run.get("team_id") is not None:
            return TeamRunSchema.from_dict(target_run).model_dump()
        else:
            return RunSchema.from_dict(target_run).model_dump()

    @register_builtin_tool(
        name="rename_session",
        description="Rename an existing session",
        tags={"session"},
    )  # type: ignore
    async def rename_session(
        session_id: str,
        session_name: str,
        db_id: str,
        session_type: str = "agent",
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)
        session_type_enum = SessionType(session_type)

        if isinstance(db, RemoteDb):
            result = await db.rename_session(
                session_id=session_id,
                session_name=session_name,
                session_type=session_type_enum,
                db_id=db_id,
            )
            return result.model_dump()

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            session = await db.rename_session(
                session_id=session_id, session_type=session_type_enum, session_name=session_name, user_id=user_id
            )
        else:
            db = cast(BaseDb, db)
            session = db.rename_session(
                session_id=session_id, session_type=session_type_enum, session_name=session_name, user_id=user_id
            )

        if not session:
            raise Exception(f"Session {session_id} not found")

        if session_type_enum == SessionType.AGENT:
            return AgentSessionDetailSchema.from_session(session).model_dump()  # type: ignore
        elif session_type_enum == SessionType.TEAM:
            return TeamSessionDetailSchema.from_session(session).model_dump()  # type: ignore
        else:
            return WorkflowSessionDetailSchema.from_session(session).model_dump()  # type: ignore

    @register_builtin_tool(
        name="update_session",
        description="Update session properties like name, state, metadata, or summary",
        tags={"session"},
    )  # type: ignore
    async def update_session(
        session_id: str,
        db_id: str,
        session_type: str = "agent",
        session_name: Optional[str] = None,
        session_state: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        summary: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)
        session_type_enum = SessionType(session_type)

        if isinstance(db, RemoteDb):
            result = await db.update_session(
                session_id=session_id,
                session_type=session_type_enum,
                session_name=session_name,
                session_state=session_state,
                metadata=metadata,
                summary=summary,
                user_id=user_id,
                db_id=db_id,
            )
            return result.model_dump()

        # Get the existing session
        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            existing_session = await db.get_session(
                session_id=session_id, session_type=session_type_enum, user_id=user_id, deserialize=True
            )
        else:
            existing_session = db.get_session(
                session_id=session_id, session_type=session_type_enum, user_id=user_id, deserialize=True
            )

        if not existing_session:
            raise Exception(f"Session {session_id} not found")

        # Update session properties
        if session_name is not None:
            if existing_session.session_data is None:  # type: ignore
                existing_session.session_data = {}  # type: ignore
            existing_session.session_data["session_name"] = session_name  # type: ignore

        if session_state is not None:
            if existing_session.session_data is None:  # type: ignore
                existing_session.session_data = {}  # type: ignore
            existing_session.session_data["session_state"] = session_state  # type: ignore

        if metadata is not None:
            existing_session.metadata = metadata  # type: ignore

        if summary is not None:
            from agno.session.summary import SessionSummary

            existing_session.summary = SessionSummary.from_dict(summary)  # type: ignore

        # Upsert the updated session
        if isinstance(db, AsyncBaseDb):
            updated_session = await db.upsert_session(existing_session, deserialize=True)  # type: ignore
        else:
            updated_session = db.upsert_session(existing_session, deserialize=True)  # type: ignore

        if not updated_session:
            raise Exception("Failed to update session")

        if session_type_enum == SessionType.AGENT:
            return AgentSessionDetailSchema.from_session(updated_session).model_dump()  # type: ignore
        elif session_type_enum == SessionType.TEAM:
            return TeamSessionDetailSchema.from_session(updated_session).model_dump()  # type: ignore
        else:
            return WorkflowSessionDetailSchema.from_session(updated_session).model_dump()  # type: ignore

    @register_builtin_tool(
        name="delete_session",
        description="Delete a specific session and all its runs",
        tags={"session"},
    )  # type: ignore
    async def delete_session(
        session_id: str,
        db_id: str,
        user_id: Optional[str] = None,
    ) -> str:
        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)

        if isinstance(db, RemoteDb):
            await db.delete_session(session_id=session_id, db_id=db_id)
            return "Session deleted successfully"

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            await db.delete_session(session_id=session_id, user_id=user_id)
        else:
            db = cast(BaseDb, db)
            db.delete_session(session_id=session_id, user_id=user_id)

        return "Session deleted successfully"

    @register_builtin_tool(
        name="delete_sessions",
        description="Delete multiple sessions by their IDs",
        tags={"session"},
    )  # type: ignore
    async def delete_sessions(
        session_ids: List[str],
        db_id: str,
        session_types: Optional[List[str]] = None,
        user_id: Optional[str] = None,
    ) -> str:
        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)

        if isinstance(db, RemoteDb):
            # Convert session_types strings to SessionType enums
            session_type_enums = [SessionType(st) for st in session_types] if session_types else []
            await db.delete_sessions(session_ids=session_ids, session_types=session_type_enums, db_id=db_id)
            return "Sessions deleted successfully"

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            await db.delete_sessions(session_ids=session_ids, user_id=user_id)
        else:
            db = cast(BaseDb, db)
            db.delete_sessions(session_ids=session_ids, user_id=user_id)

        return "Sessions deleted successfully"

    # ==================== Memory Management Tools ====================

    @register_builtin_tool(name="create_memory", description="Create a new user memory", tags={"memory"})  # type: ignore
    async def create_memory(
        db_id: str,
        memory: str,
        user_id: str,
        topics: Optional[List[str]] = None,
    ) -> UserMemorySchema:
        user_id = _resolve_user_id(user_id) or user_id
        db = await get_db(os.dbs, db_id)

        if isinstance(db, RemoteDb):
            return await db.create_memory(
                memory=memory,
                topics=topics or [],
                user_id=user_id,
                db_id=db_id,
            )

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            user_memory = await db.upsert_user_memory(
                memory=UserMemory(
                    memory_id=str(uuid4()),
                    memory=memory,
                    topics=topics or [],
                    user_id=user_id,
                ),
                deserialize=False,
            )
        else:
            db = cast(BaseDb, db)
            user_memory = db.upsert_user_memory(
                memory=UserMemory(
                    memory_id=str(uuid4()),
                    memory=memory,
                    topics=topics or [],
                    user_id=user_id,
                ),
                deserialize=False,
            )

        if not user_memory:
            raise Exception("Failed to create memory")

        return UserMemorySchema.from_dict(user_memory)  # type: ignore

    @register_builtin_tool(
        name="get_memory",
        description="Get a specific memory by ID",
        tags={"memory"},
    )  # type: ignore
    async def get_memory(
        memory_id: str,
        db_id: str,
        user_id: Optional[str] = None,
    ) -> UserMemorySchema:
        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)

        if isinstance(db, RemoteDb):
            return await db.get_memory(memory_id=memory_id, user_id=user_id, db_id=db_id)

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            user_memory = await db.get_user_memory(memory_id=memory_id, user_id=user_id, deserialize=False)
        else:
            db = cast(BaseDb, db)
            user_memory = db.get_user_memory(memory_id=memory_id, user_id=user_id, deserialize=False)

        if not user_memory:
            raise Exception(f"Memory {memory_id} not found")

        return UserMemorySchema.from_dict(user_memory)  # type: ignore

    @register_builtin_tool(
        name="get_memories",
        description="Get a paginated list of memories with optional filtering",
        tags={"memory"},
    )  # type: ignore
    async def get_memories(
        db_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        team_id: Optional[str] = None,
        topics: Optional[List[str]] = None,
        search_content: Optional[str] = None,
        limit: int = 20,
        page: int = 1,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> Dict[str, Any]:
        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)

        if isinstance(db, RemoteDb):
            result = await db.get_memories(
                user_id=user_id or "",
                agent_id=agent_id,
                team_id=team_id,
                topics=topics,
                search_content=search_content,
                limit=limit,
                page=page,
                sort_by=sort_by,
                sort_order=sort_order,
                db_id=db_id,
            )
            return result.model_dump()

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            user_memories, total_count = await db.get_user_memories(
                limit=limit,
                page=page,
                user_id=user_id,
                agent_id=agent_id,
                team_id=team_id,
                topics=topics,
                search_content=search_content,
                sort_by=sort_by,
                sort_order=sort_order,
                deserialize=False,
            )
        else:
            db = cast(BaseDb, db)
            user_memories, total_count = db.get_user_memories(
                limit=limit,
                page=page,
                user_id=user_id,
                agent_id=agent_id,
                team_id=team_id,
                topics=topics,
                search_content=search_content,
                sort_by=sort_by,
                sort_order=sort_order,
                deserialize=False,
            )

        memories = [UserMemorySchema.from_dict(m) for m in user_memories]  # type: ignore
        return {
            "data": [m.model_dump() for m in memories if m is not None],
            "meta": {
                "page": page,
                "limit": limit,
                "total_count": total_count,
                "total_pages": (total_count + limit - 1) // limit if limit > 0 else 0,  # type: ignore
            },
        }

    @register_builtin_tool(name="update_memory", description="Update an existing memory", tags={"memory"})  # type: ignore
    async def update_memory(
        db_id: str,
        memory_id: str,
        memory: str,
        user_id: str,
        topics: Optional[List[str]] = None,
    ) -> UserMemorySchema:
        user_id = _resolve_user_id(user_id) or user_id
        db = await get_db(os.dbs, db_id)

        if isinstance(db, RemoteDb):
            return await db.update_memory(
                memory_id=memory_id,
                memory=memory,
                topics=topics or [],
                user_id=user_id,
                db_id=db_id,
            )

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            user_memory = await db.upsert_user_memory(
                memory=UserMemory(
                    memory_id=memory_id,
                    memory=memory,
                    topics=topics or [],
                    user_id=user_id,
                ),
                deserialize=False,
            )
        else:
            db = cast(BaseDb, db)
            user_memory = db.upsert_user_memory(
                memory=UserMemory(
                    memory_id=memory_id,
                    memory=memory,
                    topics=topics or [],
                    user_id=user_id,
                ),
                deserialize=False,
            )

        if not user_memory:
            raise Exception("Failed to update memory")

        return UserMemorySchema.from_dict(user_memory)  # type: ignore

    @register_builtin_tool(name="delete_memory", description="Delete a specific memory by ID", tags={"memory"})  # type: ignore
    async def delete_memory(
        db_id: str,
        memory_id: str,
        user_id: Optional[str] = None,
    ) -> str:
        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)

        if isinstance(db, RemoteDb):
            await db.delete_memory(memory_id=memory_id, user_id=user_id, db_id=db_id)
            return "Memory deleted successfully"

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            await db.delete_user_memory(memory_id=memory_id, user_id=user_id)
        else:
            db = cast(BaseDb, db)
            db.delete_user_memory(memory_id=memory_id, user_id=user_id)

        return "Memory deleted successfully"

    @register_builtin_tool(
        name="delete_memories",
        description="Delete multiple memories by their IDs",
        tags={"memory"},
    )  # type: ignore
    async def delete_memories(
        memory_ids: List[str],
        db_id: str,
        user_id: Optional[str] = None,
    ) -> str:
        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)

        if isinstance(db, RemoteDb):
            await db.delete_memories(memory_ids=memory_ids, user_id=user_id, db_id=db_id)
            return "Memories deleted successfully"

        if isinstance(db, AsyncBaseDb):
            db = cast(AsyncBaseDb, db)
            await db.delete_user_memories(memory_ids=memory_ids, user_id=user_id)
        else:
            db = cast(BaseDb, db)
            db.delete_user_memories(memory_ids=memory_ids, user_id=user_id)

        return "Memories deleted successfully"

    # Register any user-provided custom tools. These share the same server, mount (/mcp),
    # lifespan, and JWT middleware as the built-in tools.
    _register_custom_tools(mcp, mcp_config)

    return mcp


def _add_authorize_middleware(mcp_app: StarletteWithLifespan, authorize: Callable[[Optional[str]], bool]) -> None:
    """Gate the MCP server with a per-call ``authorize(user_id) -> bool`` predicate.

    Runs after the JWT middleware (so ``request.state.user_id`` is the verified subject) and
    returns 401 before any tool or model runs when the predicate rejects the caller.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class _MCPAuthorizeMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
            user_id = getattr(getattr(request, "state", None), "user_id", None)
            if not authorize(user_id):
                return JSONResponse(
                    {"error": "unauthorized", "detail": "Not authorized for the MCP server."},
                    status_code=401,
                )
            return await call_next(request)

    mcp_app.add_middleware(_MCPAuthorizeMiddleware)


# Localhost defaults so a desktop / local MCP server is protected with zero extra config.
_MCP_LOCALHOST_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def _mcp_request_hostname(host_header: str) -> str:
    """Bare hostname from a Host header value, port stripped (keeps the ipv6 brackets)."""
    value = host_header.strip()
    if value.startswith("["):  # ipv6 literal, e.g. [::1]:7777
        end = value.find("]")
        return value[: end + 1] if end != -1 else value
    return value.split(":", 1)[0]


def _mcp_origin_hostname(origin: str) -> str:
    """Bare hostname from an Origin header value (keeps ipv6 brackets to match the defaults)."""
    from urllib.parse import urlparse

    hostname = urlparse(origin).hostname or ""
    return f"[{hostname}]" if ":" in hostname else hostname


def _mcp_host_allowed(hostname: str, allowed: set) -> bool:
    if hostname in allowed:
        return True
    return any(pattern.startswith("*.") and hostname.endswith(pattern[1:]) for pattern in allowed)


def _add_transport_security_middleware(
    mcp_app: StarletteWithLifespan,
    allowed_hosts: List[str],
    allowed_origins: Optional[List[str]],
) -> None:
    """Add built-in DNS-rebinding protection: validate the Host (and Origin when present).

    Allowed hosts always include localhost, so a desktop / local MCP server works out of the box;
    callers list only their deploy or tunnel host. Anything else is rejected with 400 before the
    request reaches the MCP machinery.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    host_set = {_mcp_request_hostname(h) for h in list(allowed_hosts) + list(_MCP_LOCALHOST_HOSTS)}
    origin_set = set(allowed_origins or [])

    class _MCPTransportSecurityMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
            host = _mcp_request_hostname(request.headers.get("host", ""))
            if not _mcp_host_allowed(host, host_set):
                return JSONResponse({"error": "invalid_host", "detail": "Host not allowed."}, status_code=400)
            origin = request.headers.get("origin")
            if (
                origin is not None
                and origin not in origin_set
                and not _mcp_host_allowed(_mcp_origin_hostname(origin), host_set)
            ):
                return JSONResponse({"error": "invalid_origin", "detail": "Origin not allowed."}, status_code=400)
            return await call_next(request)

    mcp_app.add_middleware(_MCPTransportSecurityMiddleware)


def get_mcp_server(
    os: "AgentOS",
) -> StarletteWithLifespan:
    """Build the MCP HTTP app served at ``/mcp``.

    Wraps :func:`build_mcp_server` with the Streamable HTTP transport and layers on (from the
    inside out) the JWT middleware (when authorization is enabled), the optional ``authorize``
    gate, any app-provided middleware, and the built-in DNS-rebinding protection -- all from
    ``mcp_config``.
    """
    mcp = build_mcp_server(os)
    mcp_config: "Optional[MCPServerConfig]" = getattr(os, "mcp_config", None)

    # Use http_app for Streamable HTTP transport (modern MCP standard)
    mcp_app = mcp.http_app(path="/mcp")

    # Middleware runs in reverse registration order (last added is outermost / runs first).
    # Target running order: transport security -> app middleware -> JWT -> authorize gate -> tool,
    # so a bad Host is rejected first and the gate sees the JWT-verified identity.

    # Innermost: per-call authorize gate.
    if mcp_config is not None and mcp_config.authorize is not None:
        # The gate reads request.state.user_id, which JWTMiddleware populates. Without a JWT
        # layer in front, that attribute is never set, so the gate sees user_id=None on every
        # call -- and an ``authorize=lambda u: u in OWNER_IDS``-style gate silently rejects
        # every request (or, worse, "allows" everyone if the gate is permissive on None). The
        # user almost always intended JWT to be on; warn loudly so this isn't a silent foot-gun.
        if not os.authorization:
            from agno.utils.log import log_warning

            log_warning(
                "MCPServerConfig.authorize is set but AgentOS(authorization=False); the gate will "
                "be called with user_id=None on every request because no JWT middleware populates "
                "request.state.user_id. Either pass authorization=True with an authorization_config, "
                "or write your authorize() to handle user_id=None explicitly (e.g. for a dev shortcut)."
            )
        _add_authorize_middleware(mcp_app, mcp_config.authorize)

    # Add JWT middleware to MCP app if authorization is enabled. Mirror the kwargs that
    # the REST surface gets in agno/os/app.py::_add_jwt_middleware -- otherwise tokens that
    # pass the REST audience check (or honour user_isolation / admin_scope) silently lose
    # those constraints over /mcp.
    if os.authorization and os.authorization_config:
        from agno.os.middleware.jwt import JWTMiddleware

        jwt_kwargs: Dict[str, Any] = {
            "verification_keys": os.authorization_config.verification_keys,
            "jwks_file": os.authorization_config.jwks_file,
            "algorithm": os.authorization_config.algorithm or "RS256",
            "authorization": os.authorization,
            "verify_audience": os.authorization_config.verify_audience or False,
        }
        if os.authorization_config.audience:
            jwt_kwargs["audience"] = os.authorization_config.audience
        if os.authorization_config.admin_scope:
            jwt_kwargs["admin_scope"] = os.authorization_config.admin_scope
        # Default to False on the middleware; only forward when actually enabled, matching the
        # REST wiring's pattern so manual JWTMiddleware defaults stay backwards-compatible.
        if os.authorization_config.user_isolation:
            jwt_kwargs["user_isolation"] = True
        # The MCP app is a separately mounted Starlette app with its own app.state, so the
        # service account verifier must be passed at construction - the middleware cannot
        # find it on the main app's state from inside the mount.
        service_account_verifier = os._get_service_account_verifier()
        if service_account_verifier is not None:
            jwt_kwargs["service_account_verifier"] = service_account_verifier
        mcp_app.add_middleware(JWTMiddleware, **jwt_kwargs)

    # App-provided middleware, preserving the order they were listed in.
    if mcp_config is not None and mcp_config.middleware:
        for mw in reversed(mcp_config.middleware):
            cls, args, kwargs = mw
            mcp_app.add_middleware(cls, *args, **kwargs)

    # Outermost: built-in DNS-rebinding protection (runs first, before auth and tools).
    if mcp_config is not None and mcp_config.allowed_hosts is not None:
        _add_transport_security_middleware(mcp_app, mcp_config.allowed_hosts, mcp_config.allowed_origins)

    return mcp_app

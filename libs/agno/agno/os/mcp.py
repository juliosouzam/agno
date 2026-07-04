"""Router for MCP interface providing Model Context Protocol endpoints."""

import functools
import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional, Union

from fastmcp import Context, FastMCP
from fastmcp.server.http import (
    StarletteWithLifespan,
)
from fastmcp.tools.tool import ToolResult

from agno.db.base import SessionType
from agno.os.mcp_results import build_run_tool_result, trim_session_run
from agno.os.schema import (
    AgentSummaryResponse,
    PaginatedResponse,
    PaginationInfo,
    SessionSchema,
    TeamSummaryResponse,
    WorkflowSummaryResponse,
)
from agno.os.services import runs as run_service
from agno.os.services import sessions as session_service
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
    # An explicitly empty include_tags set means "no built-in tools", so test against
    # None rather than truthiness.
    enabled = set(config.include_tags) if config.include_tags is not None else set(_BUILTIN_TOOL_TAGS)
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


def _forwarded_auth_headers() -> Optional[Dict[str, str]]:
    """The caller's bearer token as an Authorization header for downstream RemoteDb calls.

    Mirrors the REST routers, which forward the inbound token on every RemoteDb call so
    a JWT/PAT-protected downstream AgentOS accepts the request.
    """
    from fastmcp.server.dependencies import get_http_request

    from agno.os.auth import get_auth_token_from_request

    try:
        request = get_http_request()
    except RuntimeError:
        return None
    token = get_auth_token_from_request(request)
    return {"Authorization": f"Bearer {token}"} if token else None


def _scoped_caller_user_id() -> Optional[str]:
    """The caller's user_id when they are a non-admin, isolation-scoped principal, else None.

    Reuses the REST scoping rule (:func:`get_scoped_user_id`): admins and
    non-isolated deployments return None (no per-run ownership gate), while a
    scoped user returns their id so run-lifecycle tools can enforce ownership.
    """
    from fastmcp.server.dependencies import get_http_request

    from agno.os.middleware.user_scope import get_scoped_user_id

    try:
        request = get_http_request()
    except RuntimeError:
        return None
    return get_scoped_user_id(request)


@functools.lru_cache(maxsize=1)
def _tool_scope_mappings() -> Dict[str, List[str]]:
    """The default route→scope mappings, built once (they are static data)."""
    from agno.os.scopes import get_default_scope_mappings

    return get_default_scope_mappings()


def _require_tool_scopes(method: str, path: str) -> None:
    """Enforce the caller's scopes against the REST route this tool call is equivalent to.

    The MCP tools are an alternate transport for the REST surface, so authorization
    reuses the REST mechanism verbatim: map the tool call onto its REST route and run
    ``check_route_scopes`` with the same mappings (per-resource scopes and the admin
    bypass behave identically). Service-account scopes are ACL data enforced in every
    deployment mode, mirroring ``agno.os.auth._authenticate_service_account``; JWT
    scopes are enforced when authorization is enabled. Anonymous callers (open or
    security-key deployments) carry no scopes and pass.

    Custom ``scope_mappings`` passed to a manually-installed JWTMiddleware apply to the
    literal request path (``/mcp``), not to these synthetic routes -- the tool gate
    always enforces the default mappings.
    """
    from fastmcp.server.dependencies import get_http_request

    from agno.os.auth import build_insufficient_permissions_detail
    from agno.os.scopes import check_route_scopes

    try:
        request = get_http_request()
    except RuntimeError:
        return

    state = request.state
    is_service_account = getattr(state, "service_account_name", None) is not None
    if not is_service_account and not getattr(state, "authorization_enabled", False):
        return

    admin_scope_raw = getattr(state, "admin_scope", None)
    admin_scope = admin_scope_raw if isinstance(admin_scope_raw, str) else None
    scope_check = check_route_scopes(
        list(getattr(state, "scopes", None) or []),
        _tool_scope_mappings(),
        method,
        path,
        admin_scope=admin_scope,
    )
    if not scope_check.allowed:
        raise Exception(build_insufficient_permissions_detail(scope_check.required_scopes))


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
    """Shared run path for agents and teams: stream with progress for native components,
    plain await for everything else.

    Only native ``Agent`` / ``Team`` instances take the streaming path: remotes proxy to
    another AgentOS over HTTP and ``AgentProtocol`` implementations follow the protocol's
    streaming contract -- in both cases the streaming ``arun`` never yields the final
    output object, so they run non-streaming (no intermediate progress, same result
    contract).
    """
    from agno.agent.agent import Agent
    from agno.team.team import Team

    if not isinstance(component, (Agent, Team)):
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


def _make_lifecycle_resolver(os: "AgentOS"):
    """Bind the run-lifecycle component resolver + ownership verifier to an AgentOS.

    Both continue_run and cancel_run must resolve exactly one component and, for a
    scoped (non-admin) caller, prove the run lives in a session they own -- the same
    gate the REST cancel/continue endpoints enforce before touching a run.
    """

    def resolve(
        agent_id: Optional[str], team_id: Optional[str], workflow_id: Optional[str]
    ) -> "tuple[Any, Literal['agents', 'teams', 'workflows'], str]":
        provided = [cid for cid in (agent_id, team_id, workflow_id) if cid]
        if len(provided) != 1:
            raise Exception("Provide exactly one of agent_id, team_id, or workflow_id")
        if agent_id:
            return get_agent_by_id(agent_id, os.agents), "agents", agent_id
        if team_id:
            return get_team_by_id(team_id, os.teams), "teams", team_id
        assert workflow_id is not None  # exactly-one check above guarantees this
        return get_workflow_by_id(workflow_id, os.workflows), "workflows", workflow_id

    async def verify(
        component: Any,
        component_type: Literal["agents", "teams", "workflows"],
        component_id: str,
        session_id: Optional[str],
        run_id: str,
    ):
        if component is None:
            raise Exception(f"Component {component_id} not found")
        scoped_user_id = _scoped_caller_user_id()
        if scoped_user_id is None:
            return
        if not session_id:
            raise Exception("session_id is required to act on this run")
        try:
            await session_service.verify_run_ownership(
                component,
                session_id=session_id,
                run_id=run_id,
                user_id=scoped_user_id,
                component_type=component_type,
                component_id=component_id,
            )
        except session_service.RunOwnershipError as e:
            raise Exception(str(e))

    return resolve, verify


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

    # Component resolution + ownership gate shared by continue_run and cancel_run.
    _resolve_lifecycle_component, _verify_run_ownership = _make_lifecycle_resolver(os)

    @register_builtin_tool(
        name="get_agentos_config",
        description=(
            "Discover this AgentOS: the agents, teams, and workflows available to run (with their ids "
            "and descriptions), and the database ids used by the session tools. Call this first to learn "
            "what you can operate. The payload is deliberately compact -- the full configuration lives on "
            "the REST /config endpoint."
        ),
        tags={"core"},
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )  # type: ignore
    async def config() -> Dict[str, Any]:
        _require_tool_scopes("GET", "/config")
        return {
            "os_id": os.id or "AgentOS",
            "description": os.description,
            "databases": [db.id for db_list in os.dbs.values() for db in db_list],
            "agents": [AgentSummaryResponse.from_agent(a).model_dump() for a in os.agents] if os.agents else [],
            "teams": [TeamSummaryResponse.from_team(t).model_dump() for t in os.teams] if os.teams else [],
            "workflows": [WorkflowSummaryResponse.from_workflow(w).model_dump() for w in os.workflows]
            if os.workflows
            else [],
        }

    # ==================== Core Run Tools ====================

    @register_builtin_tool(
        name="run_agent",
        description=(
            "Run an agent with a message and get its response. Pass a session_id from get_sessions to "
            "continue that conversation; omit it to start a new one (the session_id comes back in "
            "structuredContent). If the result status is PAUSED, resolve the returned requirements and "
            "call continue_run. Agent ids come from get_agentos_config."
        ),
        tags={"core"},
        annotations={"readOnlyHint": False, "openWorldHint": True},
    )  # type: ignore
    async def run_agent(
        agent_id: str,
        message: str,
        ctx: Context,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ToolResult:
        _require_tool_scopes("POST", f"/agents/{agent_id}/runs")
        agent = get_agent_by_id(agent_id, os.agents)
        if agent is None:
            raise Exception(f"Agent {agent_id} not found")
        user_id = _resolve_user_id(user_id)
        run_output = await _run_agentic_component(
            ctx, agent, message, user_id, session_id, label=f"Agent {agent.name or agent_id}"
        )
        return build_run_tool_result(run_output, result_mode)

    @register_builtin_tool(
        name="run_team",
        description=(
            "Run a team of agents with a message and get its response. Same session and PAUSED semantics "
            "as run_agent. Team ids come from get_agentos_config."
        ),
        tags={"core"},
        annotations={"readOnlyHint": False, "openWorldHint": True},
    )  # type: ignore
    async def run_team(
        team_id: str,
        message: str,
        ctx: Context,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ToolResult:
        _require_tool_scopes("POST", f"/teams/{team_id}/runs")
        team = get_team_by_id(team_id, os.teams)
        if team is None:
            raise Exception(f"Team {team_id} not found")
        user_id = _resolve_user_id(user_id)
        run_output = await _run_agentic_component(
            ctx, team, message, user_id, session_id, label=f"Team {team.name or team_id}"
        )
        return build_run_tool_result(run_output, result_mode)

    @register_builtin_tool(
        name="run_workflow",
        description=(
            "Run a workflow with an input message and get its result. Can be long-running: progress is "
            "reported per step when the client supports it. Same session and PAUSED semantics as "
            "run_agent. Workflow ids come from get_agentos_config."
        ),
        tags={"core"},
        annotations={"readOnlyHint": False, "openWorldHint": True},
    )  # type: ignore
    async def run_workflow(
        workflow_id: str,
        message: str,
        ctx: Context,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ToolResult:
        from agno.workflow.remote import RemoteWorkflow

        _require_tool_scopes("POST", f"/workflows/{workflow_id}/runs")
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

    # ==================== Run Lifecycle Tools ====================

    @register_builtin_tool(
        name="continue_run",
        description=(
            "Resume a PAUSED run after resolving its requirements (human-in-the-loop). "
            "When a run tool returns status=PAUSED, its structuredContent carries the unresolved "
            "requirements; set the resolution fields on them (e.g. confirmation=true) and pass them "
            "back here unchanged otherwise. Provide exactly one of agent_id / team_id / workflow_id "
            "(the component that owns the run) plus the run_id and session_id from the paused result."
        ),
        tags={"core"},
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )  # type: ignore
    async def continue_run(
        run_id: str,
        ctx: Context,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        team_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        requirements: Optional[List[Dict[str, Any]]] = None,
        user_id: Optional[str] = None,
    ) -> ToolResult:
        component, component_type, component_id = _resolve_lifecycle_component(agent_id, team_id, workflow_id)
        _require_tool_scopes("POST", f"/{component_type}/{component_id}/runs/{run_id}/continue")
        user_id = _resolve_user_id(user_id)
        await _verify_run_ownership(component, component_type, component_id, session_id, run_id)
        await _report_progress(ctx, 0.0, f"Continuing run {run_id}")
        try:
            run_output = await run_service.continue_paused_run(
                component,
                run_id=run_id,
                session_id=session_id,
                user_id=user_id,
                requirements=requirements,
            )
        except run_service.RemoteContinuationUnsupported as e:
            raise Exception(str(e))
        return build_run_tool_result(run_output, result_mode)

    @register_builtin_tool(
        name="cancel_run",
        description=(
            "Request cancellation of a running run. Irreversible: the run stops and is marked CANCELLED "
            "(if it has not started yet, the intent is recorded and applied when it does). Provide the "
            "run_id, its session_id, and exactly one of agent_id / team_id / workflow_id."
        ),
        tags={"core"},
        annotations={"destructiveHint": True, "idempotentHint": True},
    )  # type: ignore
    async def cancel_run(
        run_id: str,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        team_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> str:
        component, component_type, component_id = _resolve_lifecycle_component(agent_id, team_id, workflow_id)
        _require_tool_scopes("POST", f"/{component_type}/{component_id}/runs/{run_id}/cancel")
        await _verify_run_ownership(component, component_type, component_id, session_id, run_id)
        await run_service.cancel_component_run(component, run_id)
        return f"Run {run_id} cancellation requested"

    # ==================== Session Tools (read-only) ====================
    # The MCP session surface is deliberately read-only continuity: run tools create
    # sessions implicitly, and destructive session management stays on the REST surface.

    @register_builtin_tool(
        name="get_sessions",
        description=(
            "List past sessions (conversations), newest first. Filter by session_type, component_id "
            "(an agent/team/workflow id from get_agentos_config), user, or session_name. Use a returned "
            "session_id with the run tools to continue that conversation, or with get_session_runs to "
            "read its history. db_id is only needed when get_agentos_config lists multiple databases."
        ),
        tags={"session"},
        annotations={"readOnlyHint": True},
    )  # type: ignore
    async def get_sessions(
        session_type: Literal["agent", "team", "workflow"] = "agent",
        component_id: Optional[str] = None,
        user_id: Optional[str] = None,
        session_name: Optional[str] = None,
        limit: int = 20,
        page: int = 1,
        sort_by: str = "created_at",
        sort_order: Literal["asc", "desc"] = "desc",
        db_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        _require_tool_scopes("GET", "/sessions")
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
                headers=_forwarded_auth_headers(),
            )
            return result.model_dump()

        sessions, total_count = await session_service.get_sessions_page(
            db,
            session_type=session_type_enum,
            component_id=component_id,
            user_id=user_id,
            session_name=session_name,
            limit=limit,
            page=page,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        total_pages = (total_count + limit - 1) // limit if limit > 0 else 0
        return PaginatedResponse(
            data=[SessionSchema.from_dict(session) for session in sessions],
            meta=PaginationInfo(page=page, limit=limit, total_count=total_count, total_pages=total_pages),
        ).model_dump()

    @register_builtin_tool(
        name="get_session_runs",
        description=(
            "Read a session's conversation history: each run's input and response content with its "
            "run_id, status, and timestamp, oldest first. Returns the answer content only, not the full "
            "message transcript. Pass run_id to get one run in FULL detail instead (complete transcript, "
            "events, metrics) -- the escape hatch for debugging. session_type is auto-detected when "
            "omitted; db_id is only needed when get_agentos_config lists multiple databases."
        ),
        tags={"session"},
        annotations={"readOnlyHint": True},
    )  # type: ignore
    async def get_session_runs(
        session_id: str,
        run_id: Optional[str] = None,
        session_type: Optional[Literal["agent", "team", "workflow"]] = None,
        user_id: Optional[str] = None,
        db_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        _require_tool_scopes("GET", f"/sessions/{session_id}/runs")
        user_id = _resolve_user_id(user_id)
        db = await get_db(os.dbs, db_id)
        session_type_enum = SessionType(session_type) if session_type else None

        if isinstance(db, RemoteDb):
            runs = await db.get_session_runs(
                session_id=session_id,
                session_type=session_type_enum,
                user_id=user_id,
                db_id=db_id,
                headers=_forwarded_auth_headers(),
            )
        else:
            # SessionNotFoundError propagates as the tool error verbatim ("Session {id} not found").
            runs = await session_service.get_session_runs(
                db, session_id=session_id, session_type=session_type_enum, user_id=user_id
            )

        if run_id is not None:
            for run in runs:
                data = run.model_dump() if hasattr(run, "model_dump") else dict(run)
                if data.get("run_id") == run_id:
                    return [data]
            raise Exception(f"Run {run_id} not found in session {session_id}")
        return [trim_session_run(r) for r in runs]

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


def _add_key_auth_middleware(
    mcp_app: StarletteWithLifespan,
    security_key: Optional[str],
    service_account_verifier: Optional[Any],
) -> None:
    """Enforce the REST auth rules over ``/mcp`` in non-JWT deployments.

    The REST surface enforces ``OS_SECURITY_KEY`` and service account tokens through a
    FastAPI router dependency (``agno.os.auth.get_authentication_dependency``), but router
    dependencies never run for mounted sub-apps -- without this middleware the MCP surface
    would be completely open in security-key mode. Mirrors the dependency's rules: a bearer
    (or raw) token is accepted when it matches the security key or is a valid service
    account token (``agno_pat_...``); with no security key configured, requests without a
    service account token pass through, matching REST's open mode.

    The verifier is captured at construction because the mounted MCP app has its own
    ``app.state`` -- the middleware cannot find it on the main app's state from inside
    the mount.
    """
    import hmac

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    from agno.os.auth import _is_jwt_configured
    from agno.os.service_accounts import TOKEN_PREFIX, VerificationStatus

    class _MCPKeyAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
            # Skip CORS preflight, mirroring the JWT middleware.
            if request.method == "OPTIONS":
                return await call_next(request)

            # A JWT middleware on the parent app may have already authenticated the
            # request (request.state is carried through the mount); mirror the REST
            # dependency and let it through.
            if getattr(request.state, "authenticated", False):
                return await call_next(request)

            # Accept both "Bearer <token>" and a raw token, like the JWT middleware.
            authorization = request.headers.get("Authorization", "")
            if authorization.lower().startswith("bearer "):
                token = authorization[7:].strip()
            else:
                token = authorization.strip()

            # Service account tokens are verified in every deployment mode (mirrors REST).
            if token.startswith(TOKEN_PREFIX):
                return await self._dispatch_service_account(request, token, call_next)

            # JWT configured via environment variables (manual middleware setup): the
            # REST dependency skips security key validation in this case; do the same.
            if _is_jwt_configured():
                return await call_next(request)

            # No security key configured: open instance, matching REST.
            if not security_key:
                return await call_next(request)

            if not token:
                return JSONResponse({"detail": "Authorization header required"}, status_code=401)

            if hmac.compare_digest(token, security_key):
                return await call_next(request)

            return JSONResponse({"detail": "Invalid authentication token"}, status_code=401)

        async def _dispatch_service_account(self, request, token, call_next):  # type: ignore[no-untyped-def]
            # Mirrors agno.os.auth._authenticate_service_account: uniform 401 detail for
            # unknown/revoked/expired tokens, 429 when throttled, 503 when the database
            # cannot be reached.
            if service_account_verifier is None:
                return JSONResponse(
                    {"detail": "Service accounts are not enabled on this AgentOS instance"}, status_code=401
                )

            client_key = request.client.host if request.client else None
            result = await service_account_verifier.verify(token, client_key=client_key)

            if result.status == VerificationStatus.THROTTLED:
                return JSONResponse({"detail": "Too many failed authentication attempts"}, status_code=429)
            if result.status == VerificationStatus.UNAVAILABLE:
                return JSONResponse({"detail": "Authentication is temporarily unavailable"}, status_code=503)
            account = result.account
            if not result.ok or account is None:
                return JSONResponse({"detail": "Invalid or expired service account token"}, status_code=401)

            # Attribution: _resolve_user_id reads request.state.user_id for tool calls,
            # and the tool scope gate reads scopes/authorization_enabled. Mirrors the
            # state set by agno.os.auth._authenticate_service_account.
            request.state.authenticated = True
            request.state.user_id = account.principal
            request.state.session_id = None
            request.state.scopes = list(account.scopes)
            request.state.authorization_enabled = True
            request.state.service_account_name = account.name
            return await call_next(request)

    mcp_app.add_middleware(_MCPKeyAuthMiddleware)


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
    inside out) the JWT middleware (when authorization is enabled) or the security-key /
    service-account auth middleware (otherwise), the optional ``authorize`` gate, any
    app-provided middleware, and the built-in DNS-rebinding protection -- all from
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

    # Add JWT middleware to MCP app if authorization is enabled. The kwargs come from the
    # same builder the REST surface uses (agno/os/middleware/jwt.py) -- otherwise tokens
    # that pass the REST audience check (or honour user_isolation / admin_scope) silently
    # lose those constraints over /mcp.
    if os.authorization and os.authorization_config:
        from agno.os.middleware.jwt import JWTMiddleware, build_jwt_middleware_kwargs

        # The MCP app is a separately mounted Starlette app with its own app.state, so the
        # service account verifier must be passed at construction - the middleware cannot
        # find it on the main app's state from inside the mount.
        jwt_kwargs = build_jwt_middleware_kwargs(
            os.authorization_config,
            authorization=os.authorization,
            service_account_verifier=os._get_service_account_verifier(),
        )
        mcp_app.add_middleware(JWTMiddleware, **jwt_kwargs)
    else:
        # Non-JWT deployments: the REST surface enforces OS_SECURITY_KEY and service
        # account tokens through a router dependency, which never runs for mounted
        # sub-apps -- without this, /mcp would be completely open in security-key mode.
        # Added in the same layer position as the JWT middleware above, so transport
        # security still runs outermost and the authorize gate sees the verified
        # identity. Nothing is added when auth is fully disabled (no security key and
        # no service account verifier), matching REST's open mode.
        security_key = os.settings.os_security_key if os.settings else None
        service_account_verifier = os._get_service_account_verifier()
        if security_key or service_account_verifier is not None:
            _add_key_auth_middleware(mcp_app, security_key, service_account_verifier)

    # App-provided middleware, preserving the order they were listed in.
    if mcp_config is not None and mcp_config.middleware:
        for mw in reversed(mcp_config.middleware):
            cls, args, kwargs = mw
            mcp_app.add_middleware(cls, *args, **kwargs)

    # Outermost: built-in DNS-rebinding protection (runs first, before auth and tools).
    if mcp_config is not None and mcp_config.allowed_hosts is not None:
        _add_transport_security_middleware(mcp_app, mcp_config.allowed_hosts, mcp_config.allowed_origins)

    return mcp_app

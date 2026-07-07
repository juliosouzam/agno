"""Durable Agno agents backed by DBOS.

``DBOSAgent`` wraps an Agno :class:`~agno.agent.agent.Agent` so that:

* ``run`` / ``arun`` execute as DBOS **workflows** (checkpointed, crash-recoverable), and
* every model request and every tool call executes as a DBOS **step**.

On a crash or restart, DBOS recovers the workflow and resumes from the last completed
step — the expensive LLM calls and tool side effects already made are not repeated.

Usage::

    from dbos import DBOS
    from agno.agent import Agent
    from agno.models.openai import OpenAIResponses
    from agno.integrations.dbos import DBOSAgent

    DBOS(config={"name": "geo", "system_database_url": "sqlite:///dbos.sqlite"})
    agent = Agent(model=OpenAIResponses(id="gpt-5.5"), name="geography", tools=[...])
    dbos_agent = DBOSAgent(agent)   # must be constructed BEFORE DBOS.launch()
    DBOS.launch()
    result = dbos_agent.run("What is the capital of Mexico?")
"""

from __future__ import annotations

from inspect import iscoroutinefunction
from typing import Any, Callable, Optional, Sequence, Union

try:
    from dbos import DBOS
except ImportError as e:
    raise ImportError(
        "`dbos` is not installed. Install it with `pip install agno[dbos]` (or `pip install dbos`) to use DBOSAgent."
    ) from e

from agno.integrations.dbos.utils import (
    DBOSStepConfig,
    in_workflow,
    is_dbos_wrapped,
    model_step_name,
    tool_step_name,
)
from agno.integrations.durable.base import (
    BaseDurableAgent,
    NonDurableOperationError,
    warn_non_durable_memory,
)
from agno.models.base import Model
from agno.utils.log import log_debug


class DBOSAgent(BaseDurableAgent):
    def __init__(
        self,
        agent,
        *,
        wrap_tools: bool = True,
        non_durable_tools: Optional[Sequence[str]] = None,
        model_step_config: Optional[DBOSStepConfig] = None,
        tool_step_config: Optional[Union[DBOSStepConfig, dict]] = None,
        max_recovery_attempts: int = 100,
    ) -> None:
        self._model_step_config = model_step_config or DBOSStepConfig()
        self._tool_step_config = tool_step_config or DBOSStepConfig()
        self._max_recovery_attempts = max_recovery_attempts
        # Cache of per-tool durable step callables, keyed by tool name (stable names).
        self._tool_steps: dict = {}

        super().__init__(agent, wrap_tools=wrap_tools, non_durable_tools=non_durable_tools)

        warn_non_durable_memory(self.agent)
        self._register_workflows()

    # ------------------------------------------------------------------
    # Model interception — one DBOS step per LLM request
    # ------------------------------------------------------------------
    def _wrap_model(self, model: "Model", role: str) -> None:
        orig_invoke = model.invoke
        orig_ainvoke = model.ainvoke

        # Model-level retries run *inside* the step (Agno classifies non-retryable
        # errors and does retry-with-guidance). DBOS step retries default OFF.
        step_kwargs = self._model_step_config.as_kwargs()

        sync_name = model_step_name(self.agent_id, role, model.id)
        async_name = f"{sync_name}.a"

        durable_invoke = DBOS.step(name=sync_name, **step_kwargs)(lambda **kw: orig_invoke(**kw))
        durable_ainvoke = DBOS.step(name=async_name, **step_kwargs)(_make_async_passthrough(orig_ainvoke))

        def invoke(**kwargs: Any):
            if not in_workflow():
                return orig_invoke(**kwargs)
            return durable_invoke(**kwargs)

        async def ainvoke(**kwargs: Any):
            if not in_workflow():
                return await orig_ainvoke(**kwargs)
            return await durable_ainvoke(**kwargs)

        model.invoke = invoke  # type: ignore[method-assign]
        model.ainvoke = ainvoke  # type: ignore[method-assign]

        # Streaming inside a durable run is unsupported in v1 — defense in depth.
        orig_invoke_stream = getattr(model, "invoke_stream", None)
        orig_ainvoke_stream = getattr(model, "ainvoke_stream", None)

        if orig_invoke_stream is not None:

            def invoke_stream(**kwargs: Any):
                if in_workflow():
                    raise NonDurableOperationError(
                        "Streaming is not durable in v1. Use dbos_agent.original_agent "
                        "for streaming, or run without stream=True."
                    )
                return orig_invoke_stream(**kwargs)

            model.invoke_stream = invoke_stream  # type: ignore[method-assign]

        if orig_ainvoke_stream is not None:

            def ainvoke_stream(**kwargs: Any):
                if in_workflow():
                    raise NonDurableOperationError(
                        "Streaming is not durable in v1. Use dbos_agent.original_agent "
                        "for streaming, or run without stream=True."
                    )
                return orig_ainvoke_stream(**kwargs)

            model.ainvoke_stream = ainvoke_stream  # type: ignore[method-assign]

        log_debug(f"DBOSAgent: wrapped model '{model.id}' (role={role}) as durable step '{sync_name}'")

    # ------------------------------------------------------------------
    # Tool interception — one DBOS step per tool call
    # ------------------------------------------------------------------
    def _make_tool_hook(self) -> Callable:
        # A DBOS step is cached once per (tool name, sync/async) — its NAME must be
        # stable for checkpointing — but the step body receives the CURRENT call's
        # `func` and `arguments` every time, never baking a specific call's args in.
        def _get_tool_step(name: str, is_async: bool) -> Callable:
            key = (name, is_async)
            if key not in self._tool_steps:
                cfg = self._tool_step_config
                if isinstance(cfg, dict):
                    cfg = cfg.get(name, DBOSStepConfig())
                step_kwargs = cfg.as_kwargs()
                step_name = tool_step_name(self.agent_id, name)
                if is_async:

                    async def _astep(func: Callable, arguments: dict):
                        return await func(**arguments)

                    self._tool_steps[key] = DBOS.step(name=f"{step_name}.a", **step_kwargs)(_astep)
                else:

                    def _step(func: Callable, arguments: dict):
                        return func(**arguments)

                    self._tool_steps[key] = DBOS.step(name=step_name, **step_kwargs)(_step)
            return self._tool_steps[key]

        def hook(name: str, func: Callable, arguments: dict, **_: Any):
            # Agno places this single hook in BOTH the sync and async chains. In the
            # async chain `func` is a coroutine function; return its awaitable so the
            # chain awaits it. In the sync chain `func` is plain; return the value.
            durable = not (name in self.non_durable_tools or is_dbos_wrapped(func) or not in_workflow())
            if iscoroutinefunction(func):
                if not durable:
                    return func(**arguments)
                return _get_tool_step(name, is_async=True)(func, arguments)
            if not durable:
                return func(**arguments)
            return _get_tool_step(name, is_async=False)(func, arguments)

        return hook

    # ------------------------------------------------------------------
    # Workflow registration
    # ------------------------------------------------------------------
    def _register_workflows(self) -> None:
        wf = DBOS.workflow

        @wf(name=f"agno.{self.agent_id}.run", max_recovery_attempts=self._max_recovery_attempts)
        def _run_workflow(*args: Any, **kwargs: Any):
            kwargs.pop("stream", None)
            kwargs.pop("stream_events", None)
            return self.agent.run(*args, **kwargs)

        @wf(name=f"agno.{self.agent_id}.arun", max_recovery_attempts=self._max_recovery_attempts)
        async def _arun_workflow(*args: Any, **kwargs: Any):
            kwargs.pop("stream", None)
            kwargs.pop("stream_events", None)
            return await self.agent.arun(*args, **kwargs)

        @wf(name=f"agno.{self.agent_id}.continue_run", max_recovery_attempts=self._max_recovery_attempts)
        def _continue_workflow(*args: Any, **kwargs: Any):
            return self.agent.continue_run(*args, **kwargs)

        @wf(name=f"agno.{self.agent_id}.acontinue_run", max_recovery_attempts=self._max_recovery_attempts)
        async def _acontinue_workflow(*args: Any, **kwargs: Any):
            return await self.agent.acontinue_run(*args, **kwargs)

        self._run_workflow = _run_workflow
        self._arun_workflow = _arun_workflow
        self._continue_workflow = _continue_workflow
        self._acontinue_workflow = _acontinue_workflow

    # ------------------------------------------------------------------
    # Workflow function accessors — for DBOS.start_workflow / Queue.enqueue
    # ------------------------------------------------------------------
    @property
    def run_workflow(self) -> Callable:
        """The registered sync run workflow, for ``DBOS.start_workflow`` / ``queue.enqueue``."""
        return self._run_workflow

    @property
    def arun_workflow(self) -> Callable:
        """The registered async run workflow, for ``DBOS.start_workflow_async`` / ``queue.enqueue``."""
        return self._arun_workflow

    # ------------------------------------------------------------------
    # Public API (both sync and async variants)
    # ------------------------------------------------------------------
    def run(self, *args: Any, **kwargs: Any):
        if kwargs.get("stream"):
            raise NonDurableOperationError(
                "Streaming is not durable in v1. Call dbos_agent.original_agent.run("
                "..., stream=True) for a non-durable streaming run."
            )
        return self._run_workflow(*args, **kwargs)

    async def arun(self, *args: Any, **kwargs: Any):
        if kwargs.get("stream"):
            raise NonDurableOperationError(
                "Streaming is not durable in v1. Call dbos_agent.original_agent.arun("
                "..., stream=True) for a non-durable streaming run."
            )
        return await self._arun_workflow(*args, **kwargs)

    def continue_run(self, *args: Any, **kwargs: Any):
        return self._continue_workflow(*args, **kwargs)

    async def acontinue_run(self, *args: Any, **kwargs: Any):
        return await self._acontinue_workflow(*args, **kwargs)


def _make_async_passthrough(coro_fn: Callable) -> Callable:
    async def _inner(**kwargs: Any):
        return await coro_fn(**kwargs)

    return _inner

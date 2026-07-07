"""Engine-agnostic scaffolding for durable-execution wrappers over an Agno Agent.

A durable wrapper takes a user's :class:`~agno.agent.agent.Agent`, makes a private
deep copy, and performs three surgeries on the copy so that a durable-execution
engine (DBOS, Temporal, ...) can checkpoint and recover a run:

1. Zero the agent-level retry loop (the engine owns crash recovery).
2. Replace ``invoke``/``ainvoke`` on every ``Model`` instance the copy holds with an
   engine-durable equivalent (one durable step per LLM request).
3. Append a durable tool hook so each tool execution becomes a durable step.

The Agno-side surgery is identical across engines and lives here. Only the
engine-specific closures (what a "durable step" actually does) live in the engine
subpackages (e.g. ``agno.integrations.dbos``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Iterator, Optional, Sequence, Tuple

from agno.utils.log import log_warning

if TYPE_CHECKING:
    from agno.agent.agent import Agent
    from agno.models.base import Model


class DurableExecutionError(Exception):
    """Raised when a durable wrapper is misconfigured."""


class NonDurableOperationError(DurableExecutionError):
    """Raised when an operation cannot be made durable (e.g. streaming in v1)."""


# Process-level registry of durable agent identities, to catch two wrappers that
# would collide on the same engine-registered name.
_REGISTERED_DURABLE_IDS: set = set()


class BaseDurableAgent(ABC):
    """Base class for durable-execution wrappers over an Agno ``Agent``.

    Holds a private, mutated deep copy of the user's agent. Subclasses implement the
    engine-specific step/tool wrapping; this class owns all Agno-side surgery.
    """

    def __init__(
        self,
        agent: "Agent",
        *,
        wrap_tools: bool = True,
        non_durable_tools: Optional[Sequence[str]] = None,
    ) -> None:
        self.original_agent = agent
        self.wrap_tools = wrap_tools
        self.non_durable_tools = set(non_durable_tools or [])

        self.agent_id = self._validate_identity(agent)
        self._register_identity(self.agent_id)

        # Build the private durable copy and perform the surgery.
        self.agent = self._prepare_durable_copy(agent)

    # ------------------------------------------------------------------
    # Engine contract — implemented by subclasses
    # ------------------------------------------------------------------
    @abstractmethod
    def _wrap_model(self, model: "Model", role: str) -> None:
        """Replace ``model.invoke``/``model.ainvoke`` in place with durable equivalents.

        ``role`` is one of ``{"model", "reasoning", "parser", "output", "summary",
        "fallback"}`` and is used together with ``model.id`` to build a stable,
        engine-registered step name.
        """

    @abstractmethod
    def _make_tool_hook(self) -> Callable:
        """Return a single tool hook ``hook(name, func, arguments)``.

        The hook must call ``func(**arguments)`` to continue the chain, wrapping that
        call as a durable step. A single hook (rather than a sync/async pair) avoids
        double-wrapping: Agno places the same hook in both the sync and async chains,
        so the hook inspects ``func`` at call time and adapts — returning a coroutine
        when ``func`` is async (the async chain awaits it) and a value when it is sync.
        """

    # ------------------------------------------------------------------
    # Shared Agno-side surgery
    # ------------------------------------------------------------------
    def _validate_identity(self, agent: "Agent") -> str:
        """Return a stable id for the agent, or raise if it has no stable name/id."""
        from agno.utils.string import generate_id_from_name

        if not getattr(agent, "name", None) and not getattr(agent, "id", None):
            raise DurableExecutionError(
                "Durable agents require a stable `name` (or `id`). It is used as the "
                "durable identity for engine workflow/step registration and recovery. "
                "Set Agent(name=...) before wrapping."
            )
        if getattr(agent, "id", None):
            return agent.id  # type: ignore[return-value]
        return generate_id_from_name(agent.name)  # type: ignore[arg-type]

    def _register_identity(self, agent_id: str) -> None:
        if agent_id in _REGISTERED_DURABLE_IDS:
            raise DurableExecutionError(
                f"A durable agent with id '{agent_id}' is already registered in this "
                "process. Durable agents must have unique names."
            )
        _REGISTERED_DURABLE_IDS.add(agent_id)

    def _iter_models(self, agent: "Agent") -> Iterator[Tuple[str, "Model"]]:
        """Yield ``(role, model)`` for every Model instance reachable from ``agent``."""
        from agno.models.base import Model

        seen: set = set()

        def _emit(role: str, candidate: Any) -> Iterator[Tuple[str, "Model"]]:
            if isinstance(candidate, Model) and id(candidate) not in seen:
                seen.add(id(candidate))
                yield role, candidate

        yield from _emit("model", getattr(agent, "model", None))
        yield from _emit("reasoning", getattr(agent, "reasoning_model", None))
        yield from _emit("parser", getattr(agent, "parser_model", None))
        yield from _emit("output", getattr(agent, "output_model", None))

        summary_manager = getattr(agent, "session_summary_manager", None)
        if summary_manager is not None:
            yield from _emit("summary", getattr(summary_manager, "model", None))

        fallback = getattr(agent, "fallback_config", None)
        if fallback is not None:
            for bucket in ("on_error", "on_rate_limit", "on_context_overflow"):
                for candidate in getattr(fallback, bucket, []) or []:
                    yield from _emit("fallback", candidate)

    def _isolate_models(self, agent: "Agent") -> None:
        """Deep-copy each Model instance on ``agent`` in place.

        ``Agent.deep_copy`` shares Model instances by reference; without this, wrapping
        a model on the durable copy would also mutate the user's original model.
        """
        from copy import deepcopy

        for attr in ("model", "reasoning_model", "parser_model", "output_model"):
            model = getattr(agent, attr, None)
            if model is not None:
                setattr(agent, attr, deepcopy(model))

        summary_manager = getattr(agent, "session_summary_manager", None)
        if summary_manager is not None and getattr(summary_manager, "model", None) is not None:
            summary_manager.model = deepcopy(summary_manager.model)

        fallback = getattr(agent, "fallback_config", None)
        if fallback is not None:
            from agno.models.base import Model as _Model

            for bucket in ("on_error", "on_rate_limit", "on_context_overflow"):
                models = getattr(fallback, bucket, None)
                if models:
                    setattr(
                        fallback,
                        bucket,
                        [deepcopy(m) if isinstance(m, _Model) else m for m in models],
                    )

    def _prepare_durable_copy(self, agent: "Agent") -> "Agent":
        # Isolated copy: never mutate the user's agent. Zero the agent-level retry
        # loop — the engine owns crash recovery; re-running the whole loop in-process
        # would append a duplicate step sequence.
        durable_agent = agent.deep_copy(update={"retries": 0})

        # Agent.deep_copy() shares Model instances by reference, so wrapping the copy's
        # models would leak durable-step routing back into the user's original agent.
        # Deep-copy each model instance onto the durable agent first, then wrap.
        self._isolate_models(durable_agent)

        for role, model in self._iter_models(durable_agent):
            self._wrap_model(model, role)

        if self.wrap_tools:
            hook = self._make_tool_hook()
            existing = list(durable_agent.tool_hooks or [])
            # Append so our hook is the innermost wrapper (chain is built from the
            # reversed list): our step wraps only the entrypoint, user middleware
            # hooks remain replayable orchestration code.
            durable_agent.tool_hooks = existing + [hook]

        return durable_agent

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------
    @property
    def wrapped_agent(self) -> "Agent":
        """The private durable copy (escape hatch, e.g. for AgentOS)."""
        return self.agent

    def __getattr__(self, item: str) -> Any:
        # Read-only passthrough so dbos_agent.<agent attr> works. Only called for
        # attributes not found on the wrapper itself.
        if item in ("agent", "original_agent"):
            raise AttributeError(item)
        return getattr(self.__dict__["agent"], item)


def is_generator_entrypoint(func: Callable) -> bool:
    """True if ``func`` is a (async) generator — its results can't be pickled as a step."""
    from inspect import isasyncgenfunction, isgeneratorfunction

    return isgeneratorfunction(func) or isasyncgenfunction(func)


def warn_non_durable_memory(agent: "Agent") -> None:
    """Emit a one-time warning that memory-manager LLM calls are not durable in v1."""
    if getattr(agent, "memory_manager", None) is not None:
        log_warning(
            "Durable execution does not cover MemoryManager LLM calls (they run in "
            "background threads). Memory updates are best-effort and not checkpointed."
        )

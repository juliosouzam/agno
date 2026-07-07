"""Helpers for the DBOS durable-agent integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class DBOSStepConfig:
    """Retry configuration for a DBOS step, mirroring ``DBOS.step`` kwargs.

    Kept as an Agno-owned type so we never leak ``dbos`` types into public signatures.
    Defaults keep retries OFF — model-level retries run inside the step (Agno classifies
    non-retryable errors and does retry-with-guidance that DBOS could not replicate).
    """

    retries_allowed: bool = False
    max_attempts: int = 3
    interval_seconds: float = 1.0
    backoff_rate: float = 2.0

    def as_kwargs(self) -> dict:
        return {
            "retries_allowed": self.retries_allowed,
            "max_attempts": self.max_attempts,
            "interval_seconds": self.interval_seconds,
            "backoff_rate": self.backoff_rate,
        }


def is_dbos_wrapped(func: Callable) -> bool:
    """Best-effort detection of a function already decorated with ``@DBOS.step``.

    Avoids double-wrapping a tool the user already made durable.
    """
    for attr in ("__dbos_step__", "dbos_step", "__wrapped_by_dbos__"):
        if getattr(func, attr, None):
            return True
    # DBOS wraps step/workflow functions; a wrapped function keeps a __wrapped__ ref
    # and gains dbos-specific attributes. Fall back to a name-based heuristic.
    qualname = getattr(func, "__qualname__", "")
    return "dbos" in qualname.lower() and "step" in qualname.lower()


def in_workflow() -> bool:
    """True if we are currently executing inside a DBOS workflow."""
    from dbos import DBOS

    try:
        return DBOS.workflow_id is not None
    except Exception:
        return False


def model_step_name(agent_id: str, role: str, model_id: Any) -> str:
    return f"agno.{agent_id}.{role}.{model_id}"


def tool_step_name(agent_id: str, tool_name: str) -> str:
    return f"agno.{agent_id}.tool.{tool_name}"

# DBOS Integration — Test Log

## 03_crash_recovery.py

**Status:** PASS

**Description:** End-to-end durable-execution proof using a fake model (no API key). The
agent calls a tool that hard-crashes the process (`os._exit`) on its first invocation,
after the model step has already been checkpointed. The script is run twice against a
fixed DBOS workflow id.

**Result:**
- Run 1: `REAL model invoke #1`, then the process crashed inside the tool (no output).
- Run 2: DBOS recovered the same workflow. The completed first model step was **replayed
  from its checkpoint (not re-invoked)**; only the second model turn ran fresh
  (`REAL model invoke #2`). Final output: "The capital of Mexico is Mexico City."
- Inspected the DBOS system DB (`operation_outputs`): steps
  `agno.recovery-agent.model.counting-fake` (x2) and `agno.recovery-agent.tool.note_capital`
  were checkpointed with stored outputs, confirming the naming scheme and step boundaries.

**Notes:** A tool that crashes before returning re-runs on recovery (its result was never
checkpointed) — expected durable-execution semantics; documented in README.

---

## 01_basic_durable_agent.py

**Status:** PASS (import + wiring); LLM call not run (no OPENAI_API_KEY in test env)

**Description:** Wraps an `Agent(model=OpenAIResponses("gpt-5.5"), name="geography-agent")`
with `DBOSAgent`. Verified the module imports, DBOS initializes, the wrapper is built, the
agent id resolves ("geography-agent"), and the model's `invoke` is replaced with the durable
step. The live `run()` requires an OpenAI key.

**Result:** Wrapper construction and all wiring up to the provider call succeed.

---

## 02_durable_tools.py

**Status:** PASS (import + wiring); LLM call not run (no OPENAI_API_KEY in test env)

**Description:** Two tools — `get_weather` (auto-wrapped durable step with retries via
`tool_step_config`) and `log_event` (excluded via `non_durable_tools`). Verified import,
DBOS init, wrapper build (agent id "weather-agent"), tool-hook injection, and config plumbing.

**Result:** Wrapper construction and all wiring succeed.

---

## Bug fixed: tool-argument fidelity (same tool, different args)

**Status:** FIXED + regression-tested

**Symptom:** When the model fired multiple calls to the same tool with different
arguments (e.g. `get_weather("Tokyo")` and `get_weather("Paris")`), every durable tool
step returned the FIRST call's result (all "Tokyo"). Surfaced in the Conductor step view.

**Root cause:** The tool-hook wrapper cached one DBOS step per tool name and baked the
first call's `func`/arguments into the cached closure, so later calls reused stale args.
A secondary bug in the two-hook (sync+async) design left the async coroutine un-awaited.

**Fix:** The cached step is now a generic body `(func, arguments) -> func(**arguments)`
that receives the current call's arguments every time; and the two hooks were replaced by
a single adaptive hook (returns an awaitable for async `func`, a value for sync), placed
in both Agno chains — eliminating the double-wrap.

**Regression tests:** `test_dbos_agent_e2e.py::{test_sync,test_async}_same_tool_distinct_args`
drive a fake model that fires Tokyo+Paris in one run and assert both distinct results
appear (`Paris:17|Tokyo:22`), sync and async (concurrent). Both pass.

## Static checks

- `ruff format` — clean (files reformatted to house style).
- `ruff check libs/agno/agno/integrations/{dbos,durable}/` — All checks passed.
- `mypy` on the two new packages — no errors in `integrations/dbos` or `integrations/durable`
  (repo-wide pre-existing errors in unrelated `os/` modules are unchanged).

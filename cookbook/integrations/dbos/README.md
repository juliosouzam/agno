# DBOS — Durable Agents

Make Agno agents **durable**: their runs survive crashes, transient API failures, and
process restarts. [DBOS](https://docs.dbos.dev) checkpoints each step to a database and,
on recovery, resumes a run from the last completed step instead of starting over.

`DBOSAgent` wraps a normal Agno `Agent` so that:

- `run` / `arun` execute as DBOS **workflows** (checkpointed, crash-recoverable), and
- every model request and every tool call executes as a DBOS **step**.

If the process crashes mid-run, DBOS recovers the workflow on restart and resumes from
the last completed step — the expensive LLM calls and tool results already checkpointed
are **not** recomputed.

## Install

```bash
pip install "agno[dbos]"
```

DBOS uses SQLite in these examples for zero setup. For production, point
`system_database_url` at a **PostgreSQL** instance.

## Quick start

```python
from dbos import DBOS
from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.integrations.dbos import DBOSAgent

DBOS(config={"name": "geo", "system_database_url": "sqlite:///dbos.sqlite"})

agent = Agent(model=OpenAIResponses(id="gpt-5.5"), name="geography")  # name is REQUIRED
dbos_agent = DBOSAgent(agent)   # must be built BEFORE DBOS.launch()

DBOS.launch()
result = dbos_agent.run("What is the capital of Mexico?")
print(result.content)
```

## Examples

| File | What it shows | Needs API key |
|------|---------------|:---:|
| `01_basic_durable_agent.py` | Wrap an agent; run as a durable workflow | Yes |
| `02_durable_tools.py` | Auto-wrapped tools, `non_durable_tools`, per-tool retries | Yes |
| `03_crash_recovery.py` | **Crash recovery, provably exactly-once** (fake model) | **No** |
| `04_fastapi_observability.py` | Long-running service; stays live in Conductor UI | Yes |
| `05_service_with_tools.py` | Multi-step runs with tool calls; batch endpoint | Yes |
| `06_manage_workflows_in_code.py` | List/inspect/fork/resume runs **without the console** | **No** |
| `07_background_and_queues.py` | Durable background runs + queue fan-out | **No** |

Run `03_crash_recovery.py` twice to *see* durability with no API key: the first run
crashes mid-run; the second recovers the same workflow and the already-completed model
step is replayed from its checkpoint rather than re-invoked.

```bash
python 03_crash_recovery.py   # crashes after the model step is checkpointed
python 03_crash_recovery.py   # recovers, finishes; model was invoked only once total... plus the second turn
```

## Managing runs in code (no console required)

The DBOS Conductor console is a convenience UI over APIs you can call directly. The
durability guarantee — automatic crash recovery — is in the engine and needs neither the
console nor these calls. But these let you build your own ops surface (e.g. AgentOS routes):

```python
DBOS.list_workflows(name="agno.<agent-id>.run")   # the Workflows table
DBOS.list_workflow_steps(workflow_id)             # the step waterfall
DBOS.get_workflow_status(workflow_id)
DBOS.resume_workflow(workflow_id)                 # console "Resume" (interrupted runs)
DBOS.fork_workflow(workflow_id, start_step=1)     # console "Fork" (start_step=0 == restart)
DBOS.cancel_workflow(workflow_id)
```

For durable background runs and fan-out, `DBOSAgent` exposes its workflow functions:

```python
handle = DBOS.start_workflow(dbos_agent.run_workflow, "question")   # background
result = handle.get_result()

queue = Queue("agent-runs", concurrency=2)
handles = [queue.enqueue(dbos_agent.run_workflow, q) for q in questions]
```

Note: the console's **Resume** is greyed out for a run that already SUCCEEDED (nothing to
resume). There is no separate **Restart** button — restart is `fork_workflow(id, start_step=0)`.

## Key rules

- **`name` is required** on the agent — it is the durable identity used to register
  workflows/steps and to recover them. An unnamed agent gets a random id per process.
- **Construct `DBOSAgent` before `DBOS.launch()`** so DBOS can register the functions
  it needs for recovery.
- **Retries**: the agent-level retry loop is disabled on the durable copy (DBOS owns
  crash recovery). Model-level retries still run *inside* the model step. Configure DBOS
  step retries via `model_step_config` / `tool_step_config`.

## Caveats (v1)

- **Streaming** inside a durable run is not supported — `run(stream=True)` raises. Use
  `dbos_agent.original_agent.run(..., stream=True)` for non-durable streaming.
- **Memory-manager LLM calls** run in background threads and are not durable in v1.
- **Tool results and inputs must be picklable** (DBOS checkpoints them). Exclude a tool
  with `non_durable_tools=[...]`, or accept non-durability for it.
- **Idempotency**: a step that performs a side effect and then crashes *before it
  returns* will re-run on recovery (its result was never checkpointed). Keep tool side
  effects idempotent — this is inherent to all durable-execution engines.
- **AgentOS**: register `dbos_agent.wrapped_agent` (the durable copy) with `AgentOS`, and
  call `dbos_agent.arun` from your own routes for durable entry points.

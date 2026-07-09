"""
Workflow Activity Events (opt-in AG-UI ACTIVITY channel)
========================================================

The same ``state.workflow_progress`` dict the STATE channel carries is
dual-emitted as native ACTIVITY_SNAPSHOT / ACTIVITY_DELTA events when the
interface is created with ``AGUI(..., emit_activity=True)``: a snapshot first
(clients drop deltas for unknown activity ids), RFC 6902 deltas per step
transition, and a full snapshot at every terminal (completed and error). The
activity has a stable per-run message id (``agno-workflow-progress-<run_id>``)
and activity_type ``"agno-workflow-progress"``. The flag defaults off, which
keeps the wire unchanged.

ACTIVITY is wire-only in the stock dojo today: no page registers a renderer
for this activity_type, so nothing paints until a client registers one
(CopilotKit v2)::

    const workflowProgressRenderer = {
      activityType: "agno-workflow-progress",
      render: ({ content }) => (
        <TaskProgress status={content.status} steps={content.steps} />
      ),
    };

    <CopilotKit
      runtimeUrl={`/api/copilotkit/${integrationId}`}
      agent="agentic_generative_ui"
      renderActivityMessages={[workflowProgressRenderer]}
    >

Run:
    .venvs/demo/bin/python cookbook/05_agent_os/interfaces/agui/activity_events.py
then POST an AG-UI RunAgentInput to http://localhost:9001/agui and watch the
ACTIVITY events ride beside the STATE events.
"""

from agno.agent.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.os import AgentOS
from agno.os.interfaces.agui import AGUI
from agno.workflow.step import Step
from agno.workflow.workflow import Workflow

MODEL_ID = "gpt-5.5"
PORT = 9001

researcher = Agent(
    name="Researcher",
    model=OpenAIResponses(id=MODEL_ID),
    instructions="Research the topic and return three concise factual bullets.",
    markdown=True,
)
summarizer = Agent(
    name="Summarizer",
    model=OpenAIResponses(id=MODEL_ID),
    instructions="Write a one-paragraph summary for a busy reader.",
    markdown=True,
)

activity_workflow = Workflow(
    name="Research Pipeline",
    description="Research then summarize -- progress dual-emitted as ACTIVITY events.",
    steps=[
        Step(name="research", agent=researcher),
        Step(name="summarize", agent=summarizer),
    ],
)

agent_os = AgentOS(
    workflows=[activity_workflow],
    interfaces=[AGUI(workflow=activity_workflow, emit_activity=True)],
)
app = agent_os.get_app()

if __name__ == "__main__":
    agent_os.serve(app="activity_events:app", reload=False, port=PORT)

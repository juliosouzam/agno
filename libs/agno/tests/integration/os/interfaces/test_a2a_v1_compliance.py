"""W2/W3 compliance tests: JSON-RPC dispatcher methods, typed A2A errors,
identity resolution, cancel honesty, version negotiation and agent-card fixes."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agno.agent import Agent
from agno.os import AgentOS
from agno.os.interfaces.a2a import A2A
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from agno.workflow.step import Step
from agno.workflow.workflow import Workflow

AGENT_ID = "compliance-agent"


@pytest.fixture
def agent():
    agent = Agent(id=AGENT_ID, name="Compliance Agent")
    agent.deep_copy = lambda **kwargs: agent
    return agent


@pytest.fixture
def client(agent):
    app = AgentOS(agents=[agent], a2a_interface=True).get_app()
    return TestClient(app)


def _rpc(method: str, params: dict, request_id: str = "req-1") -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def _message_params(text: str = "hi", **metadata) -> dict:
    message = {
        "messageId": "msg-1",
        "role": "ROLE_USER",
        "contextId": "ctx-1",
        "parts": [{"text": text}],
    }
    if metadata:
        message["metadata"] = metadata
    return {"message": message}


DISPATCH_URL = f"/a2a/agents/{AGENT_ID}/v1"


# --- dispatcher: task methods -----------------------------------------------------


def test_dispatcher_get_task_returns_bare_task(agent, client):
    output = RunOutput(run_id="run-1", session_id="ctx-1", content="the answer", status=RunStatus.completed)
    with patch.object(agent, "aget_run_output", new_callable=AsyncMock, return_value=output):
        resp = client.post(DISPATCH_URL, json=_rpc("GetTask", {"id": "run-1", "contextId": "ctx-1"}))

    assert resp.status_code == 200
    result = resp.json()["result"]
    # GetTask returns the Task directly — the SDK parses result as a Task proto.
    assert "task" not in result
    assert result["id"] == "run-1"
    assert result["status"]["state"] == "TASK_STATE_COMPLETED"


def test_dispatcher_get_task_without_context_id_is_invalid_params(agent, client):
    resp = client.post(DISPATCH_URL, json=_rpc("GetTask", {"id": "run-1"}))
    assert resp.status_code == 200
    error = resp.json()["error"]
    assert error["code"] == -32602
    assert "contextId" in error["message"]


def test_dispatcher_get_task_unknown_task_is_task_not_found(agent, client):
    with patch.object(agent, "aget_run_output", new_callable=AsyncMock, return_value=None):
        resp = client.post(DISPATCH_URL, json=_rpc("GetTask", {"id": "nope", "contextId": "ctx-1"}))
    assert resp.json()["error"]["code"] == -32001


def test_dispatcher_cancel_running_task_succeeds(agent, client):
    output = RunOutput(run_id="run-1", session_id="ctx-1", status=RunStatus.running)
    with (
        patch.object(agent, "aget_run_output", new_callable=AsyncMock, return_value=output),
        patch.object(agent, "acancel_run", new_callable=AsyncMock, return_value=True) as mock_cancel,
    ):
        resp = client.post(DISPATCH_URL, json=_rpc("CancelTask", {"id": "run-1", "contextId": "ctx-1"}))

    result = resp.json()["result"]
    assert result["status"]["state"] == "TASK_STATE_CANCELED"
    mock_cancel.assert_awaited_once_with(run_id="run-1")


def test_dispatcher_cancel_finished_task_is_not_cancelable(agent, client):
    output = RunOutput(run_id="run-1", session_id="ctx-1", status=RunStatus.completed)
    with patch.object(agent, "aget_run_output", new_callable=AsyncMock, return_value=output):
        resp = client.post(DISPATCH_URL, json=_rpc("CancelTask", {"id": "run-1", "contextId": "ctx-1"}))
    assert resp.json()["error"]["code"] == -32002


def test_dispatcher_cancel_unknown_task_is_task_not_found(agent, client):
    with patch.object(agent, "aget_run_output", new_callable=AsyncMock, return_value=None):
        resp = client.post(DISPATCH_URL, json=_rpc("CancelTask", {"id": "run-1", "contextId": "ctx-1"}))
    assert resp.json()["error"]["code"] == -32001


def test_cancel_accepts_context_id_from_request_metadata(agent, client):
    # CancelTaskRequest has no contextId field; the SDK can carry it in metadata.
    output = RunOutput(run_id="run-1", session_id="ctx-1", status=RunStatus.running)
    with (
        patch.object(agent, "aget_run_output", new_callable=AsyncMock, return_value=output) as mock_get,
        patch.object(agent, "acancel_run", new_callable=AsyncMock, return_value=True),
    ):
        resp = client.post(DISPATCH_URL, json=_rpc("CancelTask", {"id": "run-1", "metadata": {"contextId": "ctx-1"}}))

    assert resp.json()["result"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert mock_get.await_args.kwargs["session_id"] == "ctx-1"


# --- dispatcher: declined and unknown methods --------------------------------------


@pytest.mark.parametrize(
    "method,code",
    [
        ("CreateTaskPushNotificationConfig", -32003),
        ("GetTaskPushNotificationConfig", -32003),
        ("ListTaskPushNotificationConfigs", -32003),
        ("DeleteTaskPushNotificationConfig", -32003),
        ("ListTasks", -32004),
        ("SubscribeToTask", -32004),
        ("GetExtendedAgentCard", -32004),
        ("TotallyMadeUpMethod", -32601),
    ],
)
def test_dispatcher_declined_methods_use_spec_error_codes(client, method, code):
    resp = client.post(DISPATCH_URL, json=_rpc(method, {}))
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == code


def test_workflow_dispatcher_reports_task_methods_unsupported():
    def echo(step_input):
        return step_input.input

    workflow = Workflow(id="wf-1", name="wf", steps=[Step(name="echo", executor=echo)])
    client = TestClient(AgentOS(workflows=[workflow], a2a_interface=True).get_app())

    resp = client.post("/a2a/workflows/wf-1/v1", json=_rpc("GetTask", {"id": "x", "contextId": "y"}))
    assert resp.json()["error"]["code"] == -32004


# --- version negotiation ------------------------------------------------------------


def test_unsupported_a2a_version_is_rejected(agent, client):
    resp = client.post(
        f"/a2a/agents/{AGENT_ID}/v1/message:send",
        json=_rpc("SendMessage", _message_params()),
        headers={"A2A-Version": "0.3"},
    )
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32009


def test_supported_a2a_version_is_served(agent, client):
    output = RunOutput(run_id="run-1", session_id="ctx-1", content="ok", status=RunStatus.completed)
    with patch.object(agent, "arun", new_callable=AsyncMock, return_value=output):
        resp = client.post(
            f"/a2a/agents/{AGENT_ID}/v1/message:send",
            json=_rpc("SendMessage", _message_params()),
            headers={"A2A-Version": "1.0"},
        )
    assert resp.status_code == 200
    assert resp.json()["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


# --- identity resolution --------------------------------------------------------------


@pytest.mark.parametrize("reserved", ["sa:backup-service", "__scheduler__"])
def test_anonymous_caller_cannot_claim_reserved_principal(agent, client, reserved):
    resp = client.post(
        f"/a2a/agents/{AGENT_ID}/v1/message:send",
        json=_rpc("SendMessage", _message_params()),
        headers={"X-User-ID": reserved},
    )
    assert resp.status_code == 403


def test_anonymous_caller_user_id_is_honoured(agent, client):
    output = RunOutput(run_id="run-1", session_id="ctx-1", content="ok", status=RunStatus.completed)
    with patch.object(agent, "arun", new_callable=AsyncMock, return_value=output) as mock_arun:
        client.post(
            f"/a2a/agents/{AGENT_ID}/v1/message:send",
            json=_rpc("SendMessage", _message_params()),
            headers={"X-User-ID": "alice"},
        )
    assert mock_arun.await_args.kwargs["user_id"] == "alice"


def test_authenticated_identity_beats_client_supplied_header(agent):
    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request, call_next):
        request.state.user_id = "authenticated-user"
        return await call_next(request)

    app.include_router(A2A(agents=[agent]).get_router())
    client = TestClient(app)

    output = RunOutput(run_id="run-1", session_id="ctx-1", content="ok", status=RunStatus.completed)
    with patch.object(agent, "arun", new_callable=AsyncMock, return_value=output) as mock_arun:
        client.post(
            f"/a2a/agents/{AGENT_ID}/v1/message:send",
            json=_rpc("SendMessage", _message_params()),
            headers={"X-User-ID": "victim"},
        )
    # An authenticated caller may not attribute the run to someone else.
    assert mock_arun.await_args.kwargs["user_id"] == "authenticated-user"


# --- robustness ----------------------------------------------------------------------


def test_stream_request_without_id_does_not_crash(agent, client):
    async def event_stream(**kwargs):
        output = RunOutput(run_id="run-1", session_id="ctx-1", content="ok", status=RunStatus.completed)
        yield output

    body = {"jsonrpc": "2.0", "method": "message/stream", "params": _message_params()}  # no "id": a notification
    with patch.object(agent, "arun", side_effect=lambda **kwargs: event_stream(**kwargs)):
        resp = client.post(f"/a2a/agents/{AGENT_ID}/v1/message:stream", json=body)
    assert resp.status_code == 200


def test_non_blocking_send_returns_200(agent, client):
    output = RunOutput(run_id="run-1", session_id="ctx-1", status=RunStatus.pending)
    params = _message_params()
    params["configuration"] = {"blocking": False}
    with patch.object(agent, "arun", new_callable=AsyncMock, return_value=output):
        resp = client.post(f"/a2a/agents/{AGENT_ID}/v1/message:send", json=_rpc("SendMessage", params))
    assert resp.status_code == 200
    assert resp.json()["result"]["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"


def test_direct_tasks_get_requires_context_id(agent, client):
    resp = client.post(f"/a2a/agents/{AGENT_ID}/v1/tasks:get", json=_rpc("GetTask", {"id": "run-1"}))
    assert resp.status_code == 400


# --- agent card ----------------------------------------------------------------------


def test_card_uses_custom_prefix_in_interface_url(agent):
    app = FastAPI()
    app.include_router(A2A(agents=[agent], prefix="/custom").get_router())
    client = TestClient(app)

    resp = client.get(f"/custom/agents/{AGENT_ID}/.well-known/agent-card.json")
    assert resp.status_code == 200
    card = resp.json()
    url = card["supportedInterfaces"][0]["url"]
    assert f"/custom/agents/{AGENT_ID}/v1" in url
    assert "/a2a/" not in url


def test_card_advertises_mime_modes_and_no_placeholder_examples(agent, client):
    card = client.get(f"/a2a/agents/{AGENT_ID}/.well-known/agent-card.json").json()
    assert card["defaultInputModes"] == ["text/plain"]
    assert card["defaultOutputModes"] == ["text/plain", "application/json"]
    assert "examples" not in card["skills"][0]


def test_workflow_card_advertises_streaming():
    def echo(step_input):
        return step_input.input

    workflow = Workflow(id="wf-1", name="wf", steps=[Step(name="echo", executor=echo)])
    client = TestClient(AgentOS(workflows=[workflow], a2a_interface=True).get_app())

    card = client.get("/a2a/workflows/wf-1/.well-known/agent-card.json").json()
    assert card["capabilities"]["streaming"] is True

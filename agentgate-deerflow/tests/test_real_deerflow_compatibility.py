from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentgate_deerflow import DeerFlowActionContext, DeerFlowToolCallAdapter, core_sandbox_tool_profiles

deerflow_tools = pytest.importorskip("deerflow.sandbox.tools")
messages = pytest.importorskip("langchain_core.messages")
tool_node = pytest.importorskip("langgraph.prebuilt.tool_node")


def test_real_deerflow_base_tools_and_tool_call_request_are_supported() -> None:
    profiles = {profile.tool_name: profile for profile in core_sandbox_tool_profiles()}
    tools = (deerflow_tools.read_file_tool, deerflow_tools.write_file_tool)
    adapter = DeerFlowToolCallAdapter.from_tools(
        tools,
        (profiles["read_file"], profiles["write_file"]),
        catalog_version="real-deerflow-smoke",
    )

    message = messages.AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call-real-1",
                "name": "write_file",
                "args": {
                    "description": "Write the smoke-test file.",
                    "path": "/mnt/user-data/workspace/smoke.txt",
                    "content": "ok",
                    "append": False,
                },
                "type": "tool_call",
            }
        ],
    )
    request = tool_node.ToolCallRequest(
        tool_call=message.tool_calls[0],
        tool=deerflow_tools.write_file_tool,
        state={"messages": [message]},
        runtime=MagicMock(),
    )
    action = adapter.to_action(
        request,
        DeerFlowActionContext(task_id="task-real-1", source_turn=1),
    )

    assert action.action_id == "call-real-1"
    assert action.tool_name == "write_file"
    assert action.resource is not None
    assert action.resource.resource_id == "/mnt/user-data/workspace/smoke.txt"
    write_entry = next(entry for entry in adapter.catalog_snapshot().entries if entry.name == "write_file")
    assert set(write_entry.input_schema["properties"]) == {
        "description",
        "path",
        "content",
        "append",
    }

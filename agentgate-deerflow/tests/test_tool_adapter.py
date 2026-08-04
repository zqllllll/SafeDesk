from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from agentgate_core.contracts import ActionKind
from agentgate_deerflow import (
    DeerFlowActionContext,
    DeerFlowToolCallAdapter,
    DeerFlowToolCallError,
    ToolCallErrorCode,
    core_sandbox_tool_profiles,
)


class ReadFileArguments(BaseModel):
    description: str
    path: str
    start_line: int | None = None
    end_line: int | None = None


class WriteFileArguments(BaseModel):
    description: str
    path: str
    content: str
    append: bool = False


class FakeTool:
    def __init__(self, name: str, description: str, args_schema: type[BaseModel]) -> None:
        self.name = name
        self.description = description
        self.args_schema = args_schema

    def get_input_schema(self) -> type[BaseModel]:
        return self.args_schema


def select_profiles(*names: str):
    profiles = {profile.tool_name: profile for profile in core_sandbox_tool_profiles()}
    return tuple(profiles[name] for name in names)


def make_adapter() -> DeerFlowToolCallAdapter:
    tools = (
        FakeTool("read_file", "Read one file.", ReadFileArguments),
        FakeTool("write_file", "Write one file.", WriteFileArguments),
    )
    return DeerFlowToolCallAdapter.from_tools(
        tools,
        select_profiles("read_file", "write_file"),
        catalog_version="deerflow-test-1",
    )


def context(*, turn: int = 3, dependencies: tuple[str, ...] = ()) -> DeerFlowActionContext:
    return DeerFlowActionContext(
        task_id="task-1",
        source_turn=turn,
        required_evidence_ids=("evidence-current-file",),
        dependency_action_ids=dependencies,
    )


def test_normalizes_langchain_request_and_openai_function_shapes() -> None:
    adapter = make_adapter()
    langchain_request = SimpleNamespace(
        tool_call={"id": "call-1", "name": "read_file", "args": {"description": "Inspect", "path": "/a.txt"}}
    )
    normalized = adapter.normalize_tool_call(langchain_request)
    assert normalized.tool_call_id == "call-1"
    assert normalized.arguments["path"] == "/a.txt"

    raw_openai = {
        "id": "call-2",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": '{"description":"Write","path":"/a.txt","content":"hello","append":false}',
        },
    }
    normalized_raw = adapter.normalize_tool_call(raw_openai)
    assert normalized_raw.tool_name == "write_file"
    assert normalized_raw.arguments["append"] is False


@pytest.mark.parametrize(
    ("raw_call", "expected_code"),
    [
        ("not-a-mapping", ToolCallErrorCode.NOT_A_MAPPING),
        ({"name": "read_file", "args": {}}, ToolCallErrorCode.MISSING_CALL_ID),
        ({"id": "call", "args": {}}, ToolCallErrorCode.MISSING_TOOL_NAME),
        (
            {"id": "call", "type": "invalid_tool_call", "name": "read_file", "args": {}},
            ToolCallErrorCode.INVALID_TOOL_CALL,
        ),
        (
            {"id": "call", "type": "function", "function": {"name": "read_file", "arguments": "{"}},
            ToolCallErrorCode.INVALID_ARGUMENT_JSON,
        ),
        ({"id": "call", "name": "read_file", "args": []}, ToolCallErrorCode.ARGUMENTS_NOT_OBJECT),
    ],
)
def test_normalization_errors_have_stable_codes(raw_call: object, expected_code: ToolCallErrorCode) -> None:
    with pytest.raises(DeerFlowToolCallError) as error:
        make_adapter().normalize_tool_call(raw_call)
    assert error.value.code is expected_code


def test_read_call_preserves_call_id_resource_schema_and_dependencies() -> None:
    adapter = make_adapter()
    raw_call = {
        "id": "call-read-1",
        "name": "read_file",
        "args": {"description": "Inspect", "path": "/workspace/a.txt", "start_line": 1, "end_line": 10},
    }
    action = adapter.to_action(raw_call, context(dependencies=("call-ls-1",)))

    assert action.action_id == "call-read-1"
    assert action.kind is ActionKind.READ
    assert action.resource is not None
    assert action.resource.resource_type == "file"
    assert action.resource.resource_id == "/workspace/a.txt"
    assert action.dependency_action_ids == ("call-ls-1",)
    assert action.required_evidence_ids == ("evidence-current-file",)
    assert action.expected_effects == ()
    assert action.idempotency_key is None
    assert action.tool_schema_version.startswith("sha256:")


def test_write_call_generates_expected_effect_and_stable_semantic_idempotency_key() -> None:
    adapter = make_adapter()
    raw_call = {
        "id": "call-write-1",
        "name": "write_file",
        "args": {"description": "Write", "path": "/workspace/a.txt", "content": "hello", "append": False},
    }
    action = adapter.to_action(raw_call, context(turn=3))
    retry = adapter.to_action({**raw_call, "id": "call-write-2"}, context(turn=8))

    assert action.kind is ActionKind.WRITE
    assert action.resource is not None
    assert action.resource.resource_id == "/workspace/a.txt"
    assert action.expected_effects[0].resource == action.resource
    content_hash = hashlib.sha256(json.dumps("hello", separators=(",", ":")).encode()).hexdigest()
    assert action.expected_effects[0].expected_change == {
        "content_sha256": f"sha256:{content_hash}",
        "append": False,
    }
    assert action.idempotency_key == retry.idempotency_key
    assert action.expected_effects[0].effect_key != retry.expected_effects[0].effect_key
    assert action.required_evidence_ids == ("evidence-current-file",)

    changed_content = {
        **raw_call,
        "id": "call-write-3",
        "args": {**raw_call["args"], "content": "different"},
    }
    changed_action = adapter.to_action(changed_content, context(turn=8))
    assert changed_action.idempotency_key != action.idempotency_key


def test_unknown_tools_and_duplicate_batch_ids_fail_closed() -> None:
    adapter = make_adapter()
    with pytest.raises(DeerFlowToolCallError) as unknown_error:
        adapter.to_action({"id": "call-1", "name": "unknown", "args": {}}, context())
    assert unknown_error.value.code is ToolCallErrorCode.UNKNOWN_TOOL

    calls = [
        {"id": "same", "name": "read_file", "args": {"description": "A", "path": "/a"}},
        {"id": "same", "name": "read_file", "args": {"description": "B", "path": "/b"}},
    ]
    with pytest.raises(DeerFlowToolCallError) as duplicate_error:
        adapter.to_actions(calls, context())
    assert duplicate_error.value.code is ToolCallErrorCode.DUPLICATE_CALL_ID


def test_batch_dependencies_are_attached_by_call_id_without_inference() -> None:
    adapter = make_adapter()
    calls = [
        {"id": "read-1", "name": "read_file", "args": {"description": "Read", "path": "/a"}},
        {
            "id": "write-1",
            "name": "write_file",
            "args": {"description": "Write", "path": "/a", "content": "new", "append": False},
        },
    ]
    actions = adapter.to_actions(calls, context(), dependencies_by_call_id={"write-1": ("read-1",)})
    assert actions[0].dependency_action_ids == ()
    assert actions[1].dependency_action_ids == ("read-1",)


def test_profile_builder_refuses_unreviewed_tools() -> None:
    tools = (FakeTool("read_file", "Read.", ReadFileArguments), FakeTool("mystery", "Unknown.", ReadFileArguments))
    with pytest.raises(DeerFlowToolCallError) as error:
        DeerFlowToolCallAdapter.from_tools(
            tools,
            select_profiles("read_file"),
            catalog_version="catalog-1",
        )
    assert error.value.code is ToolCallErrorCode.MISSING_PROFILE

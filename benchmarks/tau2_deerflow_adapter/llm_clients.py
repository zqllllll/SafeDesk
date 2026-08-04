from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from tau2.data_model.message import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.environment.tool import Tool


class LLMClient(ABC):
    @abstractmethod
    def generate(
        self,
        *,
        messages: list[Message],
        tools: list[Tool],
        llm_args: dict[str, Any],
    ) -> AssistantMessage:
        raise NotImplementedError


def make_llm_client(
    *,
    backend: str,
    model: str,
    thinking_enabled: bool,
    deerflow_backend_path: str | None,
    deerflow_config_path: str | None,
) -> LLMClient:
    if backend == "deerflow":
        return DeerFlowModelClient(
            model=model,
            thinking_enabled=thinking_enabled,
            deerflow_backend_path=deerflow_backend_path,
            deerflow_config_path=deerflow_config_path,
        )
    if backend == "litellm":
        return LiteLLMClient(model=model)
    raise ValueError(f"Unknown adapter backend: {backend}")


class LiteLLMClient(LLMClient):
    """Control backend that delegates to tau2's normal LiteLLM generator."""

    def __init__(self, model: str) -> None:
        self.model = model

    def generate(
        self,
        *,
        messages: list[Message],
        tools: list[Tool],
        llm_args: dict[str, Any],
    ) -> AssistantMessage:
        from tau2.utils.llm_utils import generate

        return generate(
            model=self.model,
            messages=messages,
            tools=tools,
            call_name="deerflow_tau_litellm_agent_response",
            **llm_args,
        )


class DeerFlowModelClient(LLMClient):
    """LangChain client built through DeerFlow's create_chat_model()."""

    def __init__(
        self,
        *,
        model: str,
        thinking_enabled: bool,
        deerflow_backend_path: str | None,
        deerflow_config_path: str | None,
    ) -> None:
        _prepare_deerflow_imports(deerflow_backend_path, deerflow_config_path)

        from deerflow.models import create_chat_model

        self.model_name = model
        self.model = create_chat_model(
            name=model,
            thinking_enabled=thinking_enabled,
            attach_tracing=False,
        )

    def generate(
        self,
        *,
        messages: list[Message],
        tools: list[Tool],
        llm_args: dict[str, Any],
    ) -> AssistantMessage:
        lc_messages = [_to_langchain_message(message) for message in messages]
        tool_schemas = [tool.openai_schema for tool in tools]
        model = self.model.bind_tools(tool_schemas) if tool_schemas else self.model

        start = time.perf_counter()
        response = model.invoke(lc_messages, **llm_args)
        generation_time_seconds = time.perf_counter() - start
        return _from_langchain_ai_message(response, generation_time_seconds)


def _prepare_deerflow_imports(
    deerflow_backend_path: str | None,
    deerflow_config_path: str | None,
) -> None:
    backend = Path(
        deerflow_backend_path or os.getenv("DEERFLOW_BACKEND_PATH") or _default_deerflow_backend_path()
    ).resolve()
    repo_root = backend.parent

    harness_path = backend / "packages" / "harness"
    if harness_path.exists():
        _prepend_sys_path(harness_path)

    site_packages = _venv_site_packages(backend / ".venv")
    if site_packages and site_packages.exists():
        _prepend_sys_path(site_packages)

    if deerflow_config_path:
        os.environ.setdefault("DEER_FLOW_CONFIG_PATH", str(Path(deerflow_config_path)))
    else:
        config_path = Path(os.getenv("DEER_FLOW_CONFIG_PATH", ""))
        if not config_path.exists():
            candidate = repo_root / "config.yaml"
            if candidate.exists():
                os.environ["DEER_FLOW_CONFIG_PATH"] = str(candidate)

    if importlib.util.find_spec("deerflow") is None:
        raise RuntimeError(
            "Could not import deerflow. Set deerflow_backend_path or "
            "DEERFLOW_BACKEND_PATH to the DeerFlow backend directory."
        )


def _default_deerflow_backend_path() -> Path:
    here = Path(__file__).resolve()
    return here.parents[2] / "deer-flow-main" / "backend"


def _venv_site_packages(venv_path: Path) -> Path | None:
    if not venv_path.exists():
        return None
    candidates = sorted((venv_path / "Lib" / "site-packages",))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _prepend_sys_path(path: Path) -> None:
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)


def _to_langchain_message(message: Message):
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.messages import SystemMessage as LCSystemMessage
    from langchain_core.messages import ToolMessage as LCToolMessage

    if isinstance(message, SystemMessage):
        return LCSystemMessage(content=message.content or "")
    if isinstance(message, UserMessage):
        return HumanMessage(content=message.content or "")
    if isinstance(message, AssistantMessage):
        if message.tool_calls:
            return AIMessage(
                content=message.content or "",
                tool_calls=[
                    {
                        "id": tool_call.id or f"call_{uuid.uuid4().hex[:12]}",
                        "name": tool_call.name,
                        "args": tool_call.arguments,
                    }
                    for tool_call in message.tool_calls
                ],
            )
        return AIMessage(content=message.content or "")
    if isinstance(message, ToolMessage):
        return LCToolMessage(content=message.content or "", tool_call_id=message.id)
    raise TypeError(f"Unsupported tau2 message type: {type(message)!r}")


def _from_langchain_ai_message(response: Any, generation_time_seconds: float) -> AssistantMessage:
    content = _message_content_to_text(getattr(response, "content", ""))
    tool_calls = _extract_tool_calls(response)
    raw_data = None
    if hasattr(response, "model_dump"):
        raw_data = response.model_dump()

    return AssistantMessage(
        role="assistant",
        content=content,
        tool_calls=tool_calls or None,
        cost=0.0,
        usage=_extract_usage(response),
        raw_data=raw_data,
        generation_time_seconds=generation_time_seconds,
    )


def _message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(parts)
    return str(content)


def _extract_tool_calls(response: Any) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for raw_call in getattr(response, "tool_calls", None) or []:
        calls.append(
            ToolCall(
                id=raw_call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                name=raw_call["name"],
                arguments=raw_call.get("args") or raw_call.get("arguments") or {},
            )
        )

    if calls:
        return calls

    additional_kwargs = getattr(response, "additional_kwargs", {}) or {}
    for raw_call in additional_kwargs.get("tool_calls", []) or []:
        function = raw_call.get("function", {})
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments or "{}")
        calls.append(
            ToolCall(
                id=raw_call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                name=function.get("name") or raw_call.get("name"),
                arguments=arguments,
            )
        )
    return calls


def _extract_usage(response: Any) -> dict[str, int] | None:
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return None
    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens")
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
    if input_tokens is None and output_tokens is None:
        return None
    return {
        "prompt_tokens": int(input_tokens or 0),
        "completion_tokens": int(output_tokens or 0),
    }

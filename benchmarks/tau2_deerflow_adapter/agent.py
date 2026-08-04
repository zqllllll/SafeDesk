from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from tau2.agent.base_agent import (
    HalfDuplexAgent,
    ValidAgentInputMessage,
    is_valid_agent_history_message,
)
from tau2.data_model.message import (
    APICompatibleMessage,
    AssistantMessage,
    Message,
    MultiToolMessage,
    SystemMessage,
)
from tau2.environment.tool import Tool

from .llm_clients import LLMClient, make_llm_client

AGENT_INSTRUCTION = """
You are a customer service agent that helps the user according to the <policy> provided below.
In each turn you can either:
- Send a message to the user.
- Make a tool call.
You cannot do both at the same time.

Try to be helpful and always follow the policy. Always make sure you generate valid JSON only.
""".strip()

SYSTEM_PROMPT = """
<instructions>
{agent_instruction}
</instructions>
<policy>
{domain_policy}
</policy>
""".strip()


class DeerFlowTauAgentState(BaseModel):
    """Conversation state kept in tau2 message format."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    system_messages: list[SystemMessage]
    messages: list[APICompatibleMessage]


class DeerFlowTauAgent(HalfDuplexAgent[DeerFlowTauAgentState]):
    """A tau2 HalfDuplexAgent that delegates LLM generation to DeerFlow."""

    def __init__(
        self,
        tools: list[Tool],
        domain_policy: str,
        *,
        model: str,
        backend: str = "deerflow",
        thinking_enabled: bool = False,
        llm_args: dict | None = None,
        deerflow_backend_path: str | None = None,
        deerflow_config_path: str | None = None,
    ) -> None:
        super().__init__(tools=tools, domain_policy=domain_policy)
        self.model = model
        self.backend = backend
        self.thinking_enabled = thinking_enabled
        self.llm_args = llm_args or {}
        self.client: LLMClient = make_llm_client(
            backend=backend,
            model=model,
            thinking_enabled=thinking_enabled,
            deerflow_backend_path=deerflow_backend_path,
            deerflow_config_path=deerflow_config_path,
        )

    @property
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT.format(
            domain_policy=self.domain_policy,
            agent_instruction=AGENT_INSTRUCTION,
        )

    def get_init_state(self, message_history: list[Message] | None = None) -> DeerFlowTauAgentState:
        if message_history is None:
            message_history = []
        assert all(is_valid_agent_history_message(m) for m in message_history), (
            "Message history must contain only AssistantMessage, UserMessage, or assistant ToolMessage."
        )
        return DeerFlowTauAgentState(
            system_messages=[SystemMessage(role="system", content=self.system_prompt)],
            messages=message_history,
        )

    def generate_next_message(
        self,
        message: ValidAgentInputMessage,
        state: DeerFlowTauAgentState,
    ) -> tuple[AssistantMessage, DeerFlowTauAgentState]:
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)

        assistant_message = self.client.generate(
            messages=state.system_messages + state.messages,
            tools=self.tools,
            llm_args=self.llm_args,
        )
        state.messages.append(assistant_message)
        return assistant_message, state


def create_deerflow_tau_agent(tools, domain_policy, **kwargs):
    """Factory function registered with tau2's agent registry."""

    llm_args = kwargs.get("llm_args") or {}
    model = llm_args.pop("deerflow_model", None) or kwargs.get("model") or kwargs.get("llm") or "deepseek-v4-flash"
    backend = llm_args.pop("deerflow_backend", "deerflow")
    thinking_enabled = bool(llm_args.pop("thinking_enabled", False))
    deerflow_backend_path = llm_args.pop("deerflow_backend_path", None)
    deerflow_config_path = llm_args.pop("deerflow_config_path", None)

    return DeerFlowTauAgent(
        tools=tools,
        domain_policy=domain_policy,
        model=model,
        backend=backend,
        thinking_enabled=thinking_enabled,
        llm_args=llm_args,
        deerflow_backend_path=deerflow_backend_path,
        deerflow_config_path=deerflow_config_path,
    )

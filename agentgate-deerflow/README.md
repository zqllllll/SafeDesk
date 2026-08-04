# agentgate-deerflow

`agentgate-deerflow` connects DeerFlow lifecycle hooks to the framework-independent `agentgate-core` package.

This adapter package will translate, but will not own, SafeDesk policy or verification behavior. Its planned integration points are:

- lead-agent middleware lifecycle;
- DeerFlow thread state references;
- DeerFlow tool-call normalization;
- DeerFlow RunJournal trace persistence.

## Tool-call adapter

`DeerFlowToolCallAdapter` now normalizes LangChain `ToolCall`, OpenAI raw function-call payloads, and structural `ToolCallRequest` objects into AgentGate `ActionIR` values. It uses an explicit reviewed profile for read/write semantics and fingerprints the actual `tool_call_schema` exposed to the model.

Unknown or unprofiled tools fail closed. The adapter does not execute tools, validate arguments against JSON Schema, infer dependencies, or make policy decisions; those remain separate AgentGate stages.

Reviewed profiles are currently provided for DeerFlow's seven core sandbox tools. Additional MCP, benchmark, and application tools must register explicit profiles before conversion.

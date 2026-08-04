# agentgate-core

`agentgate-core` is the framework-independent governance engine for SafeDesk.

The package owns the shared contracts and the four SafeDesk capability areas:

- state and verification;
- guarded tool execution;
- typed failure recovery;
- managed model context.

It must not import DeerFlow, LangChain, AppWorld, tau2, or any benchmark-specific implementation. Framework and benchmark integration belongs in adapter packages.

## Package layout

```text
agentgate_core.contracts
agentgate_core.runtime
agentgate_core.state_verification
agentgate_core.tool_execution_guard
agentgate_core.recovery_controller
agentgate_core.context_manager
agentgate_core.tracing
```

## Contract protocol

Protocol version `1.0` defines immutable, strict Pydantic models for tasks, actions, evidence, effects, verification results, failures, feature modes, gate decisions, coordinator results, context packs, and trace events. Generated Draft 2020-12 JSON Schemas are checked in under `agentgate_core.schemas.v1` and tested against their source models to detect drift.

Generate the schemas after an intentional contract change:

```powershell
python -m agentgate_core.contracts.schema
```

## Phase 0 runtime foundation

The shared runtime foundation now provides:

- `AgentGateFeatureConfig` with `OFF`, `SHADOW`, and `ENFORCE` modes plus a deterministic configuration hash;
- `AgentGateCoordinator` and a typed, ordered stage protocol;
- `TraceRecorder` with recursive credential redaction and fail-closed write semantics;
- in-memory and SQLite append-only trace sinks;
- structural `TraceReplay` with task-state reconstruction;
- in-memory and SQLite `TypedStateStore` backends;
- `AgentGateRuntimeSession` for one run's task lifecycle and action coordination.

The empty Phase 0 pipeline emits a Decision and TraceEvent for Schema Guard, Dependency Scheduler, Policy Gate, and Effect Preflight without applying module policy. The four capability modules install their rules through these frozen stage interfaces.

## Typed state stores

`agentgate_core.runtime.InMemoryTypedStateStore` provides the first `TypedStateStore` backend for tests and local module development. It supports optimistic TaskState versions, event-driven transitions, idempotent typed-record writes, defensive copies, status-guarded record updates, and monotonic checkpoint restoration.

`agentgate_core.runtime.SQLiteTypedStateStore` persists the same validated aggregate in SQLite with WAL, full synchronous commits, storage revisions, restart recovery, and lost-update detection across Store instances.

## State & Verification

The first core module provides a deterministic Task Reducer, Evidence Board, explicit Effect-to-Subgoal linking, Verifier Registry, post-action environment readback, Completion Gate shadow/enforcement modes, response grounding, and trace-derived metrics. Tool results remain observed evidence; only a successful environment readback can verify an effect or subgoal.

See `docs/State与Verification模块说明.md` for contracts, runner integration order, safety boundaries, and AppWorld shadow-audit results.

See `docs/公共底座Phase0冻结说明.md` for the frozen interfaces, invariants, and known transaction boundary.

## Tool Execution Guard, Recovery, and Context

The remaining core modules provide versioned tool catalogs and active sets, JSON Schema validation, dependency and policy enforcement, guarded Effect preflight, dynamic tool resolution, typed recovery strategies and budgets, progress/stagnation detection, structured result projection, token budgeting, safe history summaries, and context invariants.

`assemble_agentgate()` builds the four-module runtime and returns a `SafeDeskOrchestrator` for the runner lifecycle.

See:

- `docs/Tool Execution Guard模块说明.md`;
- `docs/Recovery Controller模块说明.md`;
- `docs/Context Manager模块说明.md`;
- `docs/四模块运行编排说明.md`.

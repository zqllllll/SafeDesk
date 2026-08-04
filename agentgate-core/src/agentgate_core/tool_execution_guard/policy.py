"""Deterministic policy evaluation for tool actions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentgate_core.contracts.action import ActionIR, RiskLevel
from agentgate_core.contracts.decision import (
    ActionEvaluationContext,
    DecisionOutcome,
    FeatureName,
    PipelineStage,
    StageEvaluation,
)
from agentgate_core.contracts.tool_guard import (
    ArgumentOperator,
    ArgumentPolicy,
    PolicyDecision,
    PolicyEvaluationContext,
    PolicyRule,
    PolicyRuleEffect,
)

_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}
_OUTCOME_PRIORITY = {
    DecisionOutcome.ALLOW: 0,
    DecisionOutcome.REQUIRE_CONFIRMATION: 1,
    DecisionOutcome.REQUIRE_APPROVAL: 2,
    DecisionOutcome.DENY: 3,
}


class PolicyEngine:
    def __init__(self, rules: tuple[PolicyRule, ...]) -> None:
        ids = tuple(rule.rule_id for rule in rules)
        if len(ids) != len(set(ids)):
            raise ValueError("policy rule IDs must be unique")
        self.rules = rules

    def evaluate(self, action: ActionIR, context: PolicyEvaluationContext) -> PolicyDecision:
        if action.task_id != context.task_id:
            raise ValueError("policy context and action must belong to the same task")
        budget_outcome = self._budget_outcome(action, context)
        if budget_outcome is not None:
            return budget_outcome
        matched: list[PolicyRule] = []
        decisions: list[tuple[DecisionOutcome, str]] = []
        for rule in self.rules:
            if not rule.active or not _applies(rule, action):
                continue
            if rule.max_risk_level is not None and _RISK_ORDER[action.risk_level] > _RISK_ORDER[rule.max_risk_level]:
                matched.append(rule)
                decisions.append((DecisionOutcome.DENY, "Action risk exceeds the policy rule limit."))
                continue
            if rule.required_identity and not context.identity_verified:
                matched.append(rule)
                decisions.append((DecisionOutcome.DENY, "Required identity verification is missing."))
                continue
            failed_argument = next(
                (item for item in rule.argument_policies if not _argument_matches(item, action.arguments)), None
            )
            if failed_argument is not None:
                matched.append(rule)
                decisions.append((DecisionOutcome.DENY, failed_argument.message))
                continue
            outcome = _rule_outcome(rule, context)
            if outcome is not DecisionOutcome.ALLOW or rule.effect is PolicyRuleEffect.ALLOW:
                matched.append(rule)
                decisions.append((outcome, rule.description))
        if not decisions:
            return PolicyDecision(
                task_id=action.task_id,
                action_id=action.action_id,
                outcome=DecisionOutcome.ALLOW,
                reason="No active deterministic policy rule blocks this action.",
            )
        outcome, reason = max(decisions, key=lambda item: _OUTCOME_PRIORITY[item[0]])
        return PolicyDecision(
            task_id=action.task_id,
            action_id=action.action_id,
            outcome=outcome,
            matched_rule_ids=tuple(rule.rule_id for rule in matched),
            reason=reason,
        )

    @staticmethod
    def _budget_outcome(action: ActionIR, context: PolicyEvaluationContext) -> PolicyDecision | None:
        if context.max_tool_calls is not None and context.tool_call_count >= context.max_tool_calls:
            return PolicyDecision(
                task_id=action.task_id,
                action_id=action.action_id,
                outcome=DecisionOutcome.DENY,
                reason="The task tool-call budget is exhausted.",
            )
        if action.kind.value == "write" and context.max_write_actions is not None:
            if context.write_action_count >= context.max_write_actions:
                return PolicyDecision(
                    task_id=action.task_id,
                    action_id=action.action_id,
                    outcome=DecisionOutcome.DENY,
                    reason="The task write-action budget is exhausted.",
                )
        if action.resource is not None and action.resource.scope is not None and context.allowed_resource_scopes:
            if action.resource.scope not in context.allowed_resource_scopes:
                return PolicyDecision(
                    task_id=action.task_id,
                    action_id=action.action_id,
                    outcome=DecisionOutcome.DENY,
                    reason="The action resource is outside the task's allowed scopes.",
                )
        return None


class PolicyGateStage:
    stage = PipelineStage.POLICY_GATE
    feature = FeatureName.TOOL_EXECUTION_GUARD

    def __init__(self, engine: PolicyEngine) -> None:
        self.engine = engine

    def evaluate(self, context: ActionEvaluationContext) -> StageEvaluation:
        raw_policy_context = context.metadata.get("policy_context", {})
        if not isinstance(raw_policy_context, Mapping):
            raise ValueError("policy_context metadata must be an object")
        policy_context = PolicyEvaluationContext.model_validate(
            {"task_id": context.task_id, **dict(raw_policy_context)}
        )
        decision = self.engine.evaluate(context.action, policy_context)
        return StageEvaluation(
            outcome=decision.outcome,
            reason_code="policy_allowed" if decision.outcome is DecisionOutcome.ALLOW else "policy_blocked",
            explanation=decision.reason,
            payload={"policy_decision": decision.model_dump(mode="json")},
        )


def _applies(rule: PolicyRule, action: ActionIR) -> bool:
    if rule.tool_names and action.tool_name not in rule.tool_names:
        return False
    if rule.actor_kinds and action.actor not in rule.actor_kinds:
        return False
    if rule.resource_types:
        if action.resource is None or action.resource.resource_type not in rule.resource_types:
            return False
    return True


def _rule_outcome(rule: PolicyRule, context: PolicyEvaluationContext) -> DecisionOutcome:
    if rule.effect is PolicyRuleEffect.DENY:
        return DecisionOutcome.DENY
    if rule.effect is PolicyRuleEffect.REQUIRE_CONFIRMATION:
        assert rule.confirmation_id is not None
        return (
            DecisionOutcome.ALLOW
            if rule.confirmation_id in context.confirmation_ids
            else DecisionOutcome.REQUIRE_CONFIRMATION
        )
    if rule.effect is PolicyRuleEffect.REQUIRE_APPROVAL:
        assert rule.approval_id is not None
        return DecisionOutcome.ALLOW if rule.approval_id in context.approval_ids else DecisionOutcome.REQUIRE_APPROVAL
    return DecisionOutcome.ALLOW


def _argument_matches(policy: ArgumentPolicy, arguments: Mapping[str, Any]) -> bool:
    value: Any = arguments
    exists = True
    for segment in policy.path:
        if not isinstance(value, Mapping) or segment not in value:
            exists = False
            value = None
            break
        value = value[segment]
    if policy.operator is ArgumentOperator.EXISTS:
        return exists is bool(policy.expected if policy.expected is not None else True)
    if not exists:
        return False
    if policy.operator is ArgumentOperator.EQUALS:
        return value == policy.expected
    if policy.operator is ArgumentOperator.IN:
        return isinstance(policy.expected, list) and value in policy.expected
    if policy.operator is ArgumentOperator.MINIMUM:
        actual_number = _number(value)
        expected_number = _number(policy.expected)
        return actual_number is not None and expected_number is not None and actual_number >= expected_number
    if policy.operator is ArgumentOperator.MAXIMUM:
        actual_number = _number(value)
        expected_number = _number(policy.expected)
        return actual_number is not None and expected_number is not None and actual_number <= expected_number
    return False


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


__all__ = ["PolicyEngine", "PolicyGateStage"]

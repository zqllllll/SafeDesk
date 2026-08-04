"""Convert reviewed tau2 policy descriptors into deterministic AgentGate rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentgate_core.contracts import (
    ActorKind,
    ArgumentOperator,
    ArgumentPolicy,
    PolicyRule,
    PolicyRuleEffect,
    RiskLevel,
)


class Tau2PolicyAdapter:
    """Validate reviewed policy JSON; natural-language policy is never auto-enforced."""

    SUPPORTED_DOMAINS = frozenset({"telecom", "airline", "retail"})

    def from_reviewed_mapping(self, payload: Mapping[str, Any]) -> tuple[PolicyRule, ...]:
        domain = payload.get("domain")
        if domain not in self.SUPPORTED_DOMAINS:
            raise ValueError(f"unsupported tau2 domain: {domain}")
        if payload.get("reviewed") is not True:
            raise ValueError("tau2 policy draft must be explicitly reviewed before enforcement")
        raw_rules = payload.get("rules")
        if not isinstance(raw_rules, Sequence) or isinstance(raw_rules, str):
            raise ValueError("tau2 policy rules must be an array")
        rules: list[PolicyRule] = []
        for raw in raw_rules:
            if not isinstance(raw, Mapping):
                raise ValueError("each tau2 policy rule must be an object")
            rules.append(self._rule(domain, raw))
        return tuple(rules)

    @staticmethod
    def _rule(domain: str, raw: Mapping[str, Any]) -> PolicyRule:
        arguments = raw.get("argument_policies", [])
        if not isinstance(arguments, Sequence) or isinstance(arguments, str):
            raise ValueError("argument_policies must be an array")
        argument_policies = tuple(
            ArgumentPolicy(
                path=tuple(item["path"]),
                operator=ArgumentOperator(item["operator"]),
                expected=item.get("expected"),
                message=item["message"],
            )
            for item in arguments
            if isinstance(item, Mapping)
        )
        return PolicyRule(
            rule_id=f"tau2.{domain}.{raw['rule_id']}",
            description=raw["description"],
            effect=PolicyRuleEffect(raw["effect"]),
            tool_names=tuple(raw.get("tool_names", ())),
            actor_kinds=tuple(ActorKind(item) for item in raw.get("actor_kinds", ())),
            resource_types=tuple(raw.get("resource_types", ())),
            max_risk_level=(RiskLevel(raw["max_risk_level"]) if raw.get("max_risk_level") else None),
            required_identity=raw.get("required_identity", False),
            confirmation_id=raw.get("confirmation_id"),
            approval_id=raw.get("approval_id"),
            argument_policies=argument_policies,
        )


__all__ = ["Tau2PolicyAdapter"]

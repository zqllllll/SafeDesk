"""Rule-first failure classification."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from agentgate_core.contracts.failure import FailureRecord, FailureType, ResponsibleLayer
from agentgate_core.contracts.recovery import FailureSignal
from agentgate_core.contracts.verification import VerificationStatus


class FailureClassifier:
    def __init__(self, *, id_factory: Callable[[], str] | None = None) -> None:
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def classify(self, signal: FailureSignal, *, recovery_budget: int) -> FailureRecord:
        failure_type, layer, retryable, message = self._classify(signal)
        return FailureRecord(
            failure_id=f"failure-{self._id_factory()}",
            task_id=signal.task_id,
            action_id=signal.action.action_id if signal.action is not None else None,
            failure_type=failure_type,
            message=message,
            retryable=retryable,
            responsible_layer=layer,
            recovery_budget_remaining=recovery_budget,
        )

    @staticmethod
    def _classify(signal: FailureSignal) -> tuple[FailureType, ResponsibleLayer, bool, str]:
        reason = (signal.guard_reason_code or "").lower()
        error = (signal.tool_error_code or "").lower()
        if signal.infrastructure_error:
            return FailureType.INFRASTRUCTURE_ERROR, ResponsibleLayer.INFRASTRUCTURE, True, "Infrastructure failed."
        if signal.repeated_call:
            return FailureType.DUPLICATE_ACTION, ResponsibleLayer.MODEL, False, "The normalized action was repeated."
        if signal.metadata.get("unintended_side_effect") is True:
            return (
                FailureType.UNINTENDED_SIDE_EFFECT,
                ResponsibleLayer.VERIFIER,
                False,
                "Verification found an unintended side effect.",
            )
        if signal.metadata.get("wrong_tool") is True:
            return FailureType.WRONG_TOOL, ResponsibleLayer.MODEL, True, "The selected tool cannot satisfy the goal."
        if signal.metadata.get("wrong_resource") is True:
            return FailureType.WRONG_RESOURCE, ResponsibleLayer.MODEL, True, "The action targeted the wrong resource."
        if reason in {"tool_not_found", "tool_not_active", "out_of_schema"}:
            return FailureType.OUT_OF_SCHEMA, ResponsibleLayer.TOOL_SCHEMA, True, "The requested tool is unavailable."
        if reason in {"invalid_tool_arguments", "required", "type", "enum", "range", "pattern"}:
            return FailureType.INVALID_ARGUMENT, ResponsibleLayer.TOOL_SCHEMA, True, "Tool arguments are invalid."
        if reason == "schema_version_mismatch":
            return FailureType.CONTEXT_DEGRADED, ResponsibleLayer.CONTEXT, True, "The action uses a stale schema."
        if reason == "dependencies_unsatisfied":
            return FailureType.DEPENDENCY_NOT_SATISFIED, ResponsibleLayer.SCHEDULER, True, "Dependencies are missing."
        if signal.guard_outcome is not None:
            if signal.guard_outcome.value == "require_confirmation":
                return FailureType.POLICY_DENIED, ResponsibleLayer.POLICY, True, "User confirmation is required."
            if signal.guard_outcome.value == "require_approval":
                return FailureType.APPROVAL_REQUIRED, ResponsibleLayer.POLICY, True, "Approval is required."
            if signal.guard_outcome.value == "deny" and reason.startswith("policy"):
                return FailureType.POLICY_DENIED, ResponsibleLayer.POLICY, False, "Policy denied the action."
        if signal.verification_status in {VerificationStatus.MISMATCH, VerificationStatus.ERROR}:
            return FailureType.VERIFICATION_FAILED, ResponsibleLayer.VERIFIER, True, "Post-action verification failed."
        if signal.metadata.get("partial_completion") is True:
            return FailureType.PARTIAL_COMPLETION, ResponsibleLayer.MODEL, True, "Only part of the task is complete."
        if (
            not signal.state_changed
            and not signal.evidence_changed
            and signal.metadata.get("progress_expected") is True
        ):
            return (
                FailureType.NO_PROGRESS,
                ResponsibleLayer.MODEL,
                True,
                "The turn produced no state or evidence progress.",
            )
        if any(term in error for term in ("timeout", "timed_out")):
            return FailureType.TOOL_TIMEOUT, ResponsibleLayer.TOOL, True, "The tool timed out."
        if any(term in error for term in ("rate", "429")):
            return FailureType.RATE_LIMITED, ResponsibleLayer.INFRASTRUCTURE, True, "The service rate-limited the call."
        if any(term in error for term in ("auth", "credential", "unauthorized")):
            return FailureType.AUTHENTICATION_FAILED, ResponsibleLayer.TOOL, True, "Authentication failed."
        if any(term in error for term in ("permission", "forbidden", "403")):
            return FailureType.PERMISSION_DENIED, ResponsibleLayer.POLICY, False, "Permission was denied."
        if any(term in error for term in ("not_found", "wrong_resource", "404")):
            return FailureType.WRONG_RESOURCE, ResponsibleLayer.MODEL, True, "The target resource was not found."
        if signal.tool_error_code or signal.tool_error_message:
            return (
                FailureType.TOOL_EXECUTION_ERROR,
                ResponsibleLayer.TOOL,
                True,
                signal.tool_error_message or "The tool returned an execution error.",
            )
        return FailureType.MISSING_EVIDENCE, ResponsibleLayer.MODEL, True, "Required execution evidence is missing."


__all__ = ["FailureClassifier"]

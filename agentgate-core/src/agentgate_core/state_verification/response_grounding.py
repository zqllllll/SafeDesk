"""Ground final-response completion claims in Completion Gate evidence."""

from __future__ import annotations

import re
from collections.abc import Callable
from uuid import uuid4

from agentgate_core.contracts.state_verification import (
    CompletionBlockerType,
    CompletionGateDecision,
    ResponseClaim,
    ResponseClaimType,
    ResponseGroundingDecision,
)
from agentgate_core.contracts.task import SubgoalStatus
from agentgate_core.contracts.trace import TraceActor, TraceEventType
from agentgate_core.runtime.session import AgentGateRuntimeSession

_PATTERNS: tuple[tuple[ResponseClaimType, re.Pattern[str]], ...] = (
    (ResponseClaimType.WAITING_APPROVAL, re.compile(r"(?:等待|需要).{0,12}(?:审批|批准)|waiting for approval", re.I)),
    (
        ResponseClaimType.WAITING_CONFIRMATION,
        re.compile(r"(?:等待|需要).{0,12}(?:确认|同意)|waiting for confirmation", re.I),
    ),
    (ResponseClaimType.PARTIAL, re.compile(r"部分完成|完成了一部分|partially complete|part of the task", re.I)),
    (
        ResponseClaimType.FAILURE,
        re.compile(r"(?:未能|无法|没有|尚未).{0,12}(?:完成|成功)|failed|could not complete", re.I),
    ),
    (
        ResponseClaimType.SUCCESS,
        re.compile(
            r"(?<!未)(?<!尚未)(?:已完成|已经完成|成功完成|任务完成)|successfully completed|task is complete", re.I
        ),
    ),
)


class ResponseGroundingGate:
    def __init__(
        self,
        session: AgentGateRuntimeSession,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.session = session
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def evaluate(
        self,
        task_id: str,
        response: str,
        completion: CompletionGateDecision,
        *,
        turn: int,
    ) -> ResponseGroundingDecision:
        claims = self.extract_claims(response)
        requested = claims[0].claim_type if claims else ResponseClaimType.UNKNOWN
        grounded = self._grounded_status(task_id, completion)
        should_downgrade = (requested is ResponseClaimType.SUCCESS and grounded is not ResponseClaimType.SUCCESS) or (
            requested is ResponseClaimType.PARTIAL and grounded is ResponseClaimType.FAILURE
        )
        final_status = grounded if should_downgrade else requested
        grounded_response = _truthful_response(response, final_status) if should_downgrade else response
        decision = ResponseGroundingDecision(
            decision_id=f"response-grounding-{self._id_factory()}",
            task_id=task_id,
            run_id=self.session.run_id,
            original_response=response,
            grounded_response=grounded_response,
            requested_status=requested,
            grounded_status=final_status,
            claims=claims,
            completion_decision_id=completion.decision_id,
            evidence_ids=completion.evidence_ids,
            downgraded=should_downgrade,
            reason=(
                "A success claim was downgraded because Completion Gate found unresolved blockers."
                if should_downgrade
                else "The response does not overstate the evidence-backed completion status."
            ),
        )
        self.session.trace_recorder.record(
            task_id=task_id,
            run_id=self.session.run_id,
            turn=turn,
            event_type=TraceEventType.RESPONSE_GROUNDING_DECISION,
            actor=TraceActor.VERIFIER,
            correlation_id=decision.decision_id,
            state_version=self.session.state_store.get_task_state(task_id).state_version,
            payload={"response_grounding": decision.model_dump(mode="json")},
            critical=True,
        )
        return decision

    def extract_claims(self, response: str) -> tuple[ResponseClaim, ...]:
        matches: list[tuple[int, int, ResponseClaimType, str]] = []
        for claim_type, pattern in _PATTERNS:
            for match in pattern.finditer(response):
                matches.append((match.start(), match.end(), claim_type, match.group(0)))
        matches.sort(key=lambda item: (item[0], item[1]))
        return tuple(
            ResponseClaim(
                claim_id=f"claim-{self._id_factory()}",
                claim_type=claim_type,
                text=text,
                start_offset=start,
                end_offset=end,
            )
            for start, end, claim_type, text in matches
        )

    def _grounded_status(self, task_id: str, completion: CompletionGateDecision) -> ResponseClaimType:
        if completion.proposed_allowed:
            return ResponseClaimType.SUCCESS
        blocker_types = {item.blocker_type for item in completion.blockers}
        if CompletionBlockerType.PENDING_APPROVAL in blocker_types:
            return ResponseClaimType.WAITING_APPROVAL
        if CompletionBlockerType.PENDING_CONFIRMATION in blocker_types:
            return ResponseClaimType.WAITING_CONFIRMATION
        state = self.session.state_store.get_task_state(task_id)
        if any(item.status is SubgoalStatus.COMPLETED_VERIFIED for item in state.subgoals):
            return ResponseClaimType.PARTIAL
        return ResponseClaimType.FAILURE


def _truthful_response(original: str, status: ResponseClaimType) -> str:
    chinese = bool(re.search(r"[\u4e00-\u9fff]", original))
    messages = {
        ResponseClaimType.PARTIAL: (
            "任务仅部分完成，仍有目标或验证未完成。",
            "The task is only partially complete; goals or verification remain.",
        ),
        ResponseClaimType.FAILURE: (
            "任务尚未完成，当前没有足够的执行证据支持成功结论。",
            "The task is not complete; execution evidence does not support a success claim.",
        ),
        ResponseClaimType.WAITING_CONFIRMATION: (
            "任务尚未完成，正在等待必要的用户确认。",
            "The task is not complete and is waiting for required user confirmation.",
        ),
        ResponseClaimType.WAITING_APPROVAL: (
            "任务尚未完成，正在等待必要的审批。",
            "The task is not complete and is waiting for required approval.",
        ),
    }
    pair = messages.get(status, ("任务状态尚不确定。", "The task status is still unknown."))
    return pair[0] if chinese else pair[1]


__all__ = ["ResponseGroundingGate"]

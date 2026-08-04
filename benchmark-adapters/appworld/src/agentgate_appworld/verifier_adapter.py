"""AppWorld public API readback adapter for AgentGate post-action verification."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from agentgate_core.contracts.effect import EffectRecord
from agentgate_core.contracts.state_verification import VerificationObservation, VerifierSpec

AppWorldApiExecutor = Callable[[str, str, dict[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class AppWorldReadbackProfile:
    resource_type: str
    app_name: str
    api_name: str
    resource_id_argument: str | None = None
    response_path: tuple[str, ...] = ()


class AppWorldEnvironmentVerifier:
    """Read state through an injected public AppWorld API executor, never the evaluator."""

    def __init__(
        self,
        executor: AppWorldApiExecutor,
        profiles: tuple[AppWorldReadbackProfile, ...],
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._executor = executor
        self._profiles = {profile.resource_type: profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("AppWorld readback resource types must be unique")
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def observe(self, effect: EffectRecord, spec: VerifierSpec, attempt: int) -> VerificationObservation:
        profile = self._profiles.get(effect.resource.resource_type)
        if profile is None:
            return self._error(effect, "profile_missing", "No public AppWorld readback profile is configured.")
        raw_arguments = spec.adapter_config.get("read_arguments", {})
        if not isinstance(raw_arguments, Mapping):
            return self._error(effect, "read_arguments_invalid", "Configured read_arguments must be an object.")
        arguments = dict(raw_arguments)
        if profile.resource_id_argument is not None:
            if effect.resource.resource_id is None and spec.require_exact_resource_id:
                return self._error(effect, "resource_id_missing", "Readback requires an exact resource ID.")
            if effect.resource.resource_id is not None:
                arguments[profile.resource_id_argument] = effect.resource.resource_id
        try:
            payload = self._executor(profile.app_name, profile.api_name, arguments)
            observed = _extract_object(payload, profile.response_path)
        except Exception as exc:
            return self._error(effect, "readback_error", f"Public AppWorld readback failed: {type(exc).__name__}.")
        return VerificationObservation(
            task_id=effect.task_id,
            effect_id=effect.effect_id,
            source_event_id=f"appworld-readback-{self._id_factory()}",
            observed_state=observed,
        )

    def _error(self, effect: EffectRecord, code: str, message: str) -> VerificationObservation:
        return VerificationObservation(
            task_id=effect.task_id,
            effect_id=effect.effect_id,
            source_event_id=f"appworld-readback-{self._id_factory()}",
            error_code=code,
            error_message=message,
        )


def _extract_object(payload: Any, path: tuple[str, ...]) -> dict[str, Any]:
    current = payload
    for segment in path:
        if not isinstance(current, Mapping) or segment not in current:
            raise ValueError(f"readback response path is missing: {segment}")
        current = current[segment]
    if not isinstance(current, Mapping):
        raise TypeError("readback response must resolve to a JSON object")
    return dict(current)


__all__ = ["AppWorldApiExecutor", "AppWorldEnvironmentVerifier", "AppWorldReadbackProfile"]

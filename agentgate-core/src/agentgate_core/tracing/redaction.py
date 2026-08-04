"""Deterministic recursive redaction for persisted trace payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agentgate_core.contracts.trace import RedactionMetadata

REDACTED_VALUE = "[REDACTED]"
DEFAULT_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "card_number",
        "credit_card_number",
        "cvv",
        "password",
        "passwords",
        "secret",
        "token",
    }
)


@dataclass(frozen=True)
class RedactionResult:
    payload: dict[str, Any]
    metadata: RedactionMetadata


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    return str(value)


class KeyBasedTraceRedactor:
    def __init__(
        self,
        *,
        sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS,
        policy_version: str = "trace-key-redaction-v1",
    ) -> None:
        self._sensitive_keys = frozenset(key.lower() for key in sensitive_keys)
        self.policy_version = policy_version

    def redact(self, payload: Mapping[str, Any]) -> RedactionResult:
        redacted_fields: list[str] = []

        def visit(value: Any, path: str) -> Any:
            if isinstance(value, Mapping):
                output: dict[str, Any] = {}
                for raw_key, item in value.items():
                    key = str(raw_key)
                    child_path = f"{path}.{key}" if path else key
                    normalized = key.strip().lower()
                    is_sensitive = (
                        normalized in self._sensitive_keys
                        or normalized.endswith("_password")
                        or normalized.endswith("_secret")
                        or normalized.endswith("_token")
                    )
                    if is_sensitive and item is not None:
                        output[key] = REDACTED_VALUE
                        redacted_fields.append(child_path)
                    else:
                        output[key] = visit(item, child_path)
                return output
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                return [visit(item, f"{path}[{index}]") for index, item in enumerate(value)]
            return _json_safe(value)

        sanitized = visit(payload, "")
        assert isinstance(sanitized, dict)
        return RedactionResult(
            payload=sanitized,
            metadata=RedactionMetadata(
                redacted=bool(redacted_fields),
                redacted_fields=tuple(redacted_fields),
                policy_version=self.policy_version if redacted_fields else None,
            ),
        )


__all__ = [
    "DEFAULT_SENSITIVE_KEYS",
    "KeyBasedTraceRedactor",
    "REDACTED_VALUE",
    "RedactionResult",
]

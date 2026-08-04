"""Reviewed semantics for DeerFlow's core sandbox tools."""

from agentgate_core.contracts import ActionKind, EffectKind, RiskLevel
from agentgate_deerflow.tool_profile import ArgumentProjection, DeerFlowToolProfile, ExpectedChangeBinding


def core_sandbox_tool_profiles() -> tuple[DeerFlowToolProfile, ...]:
    """Return explicit profiles for the seven core DeerFlow sandbox tools."""

    return (
        DeerFlowToolProfile(
            tool_name="bash",
            operation="execute_command",
            action_kind=ActionKind.WRITE,
            risk_level=RiskLevel.CRITICAL,
            side_effect_type=EffectKind.OTHER,
            resource_type="sandbox",
            default_scope="task",
            expected_change_bindings=(ExpectedChangeBinding(output_field="command", argument_path=("command",)),),
            idempotency_paths=(("command",),),
            verification_strategy="command_specific_readback",
            idempotency_strategy="canonical_selected_arguments",
        ),
        DeerFlowToolProfile(
            tool_name="ls",
            operation="list_directory",
            action_kind=ActionKind.READ,
            risk_level=RiskLevel.LOW,
            resource_type="directory",
            resource_id_path=("path",),
        ),
        DeerFlowToolProfile(
            tool_name="glob",
            operation="glob_paths",
            action_kind=ActionKind.READ,
            risk_level=RiskLevel.LOW,
            resource_type="directory",
            resource_id_path=("path",),
        ),
        DeerFlowToolProfile(
            tool_name="grep",
            operation="search_text",
            action_kind=ActionKind.READ,
            risk_level=RiskLevel.LOW,
            resource_type="directory",
            resource_id_path=("path",),
        ),
        DeerFlowToolProfile(
            tool_name="read_file",
            operation="read_file",
            action_kind=ActionKind.READ,
            risk_level=RiskLevel.LOW,
            resource_type="file",
            resource_id_path=("path",),
        ),
        DeerFlowToolProfile(
            tool_name="write_file",
            operation="write_file",
            action_kind=ActionKind.WRITE,
            risk_level=RiskLevel.HIGH,
            side_effect_type=EffectKind.OTHER,
            resource_type="file",
            resource_id_path=("path",),
            expected_change_bindings=(
                ExpectedChangeBinding(
                    output_field="content_sha256",
                    argument_path=("content",),
                    projection=ArgumentProjection.SHA256,
                ),
                ExpectedChangeBinding(output_field="append", argument_path=("append",)),
            ),
            idempotency_paths=(("path",), ("content",), ("append",)),
            required_evidence=("current_file_version",),
            dependency_tool_names=("read_file",),
            verification_strategy="file_content_readback",
            idempotency_strategy="canonical_selected_arguments",
        ),
        DeerFlowToolProfile(
            tool_name="str_replace",
            operation="replace_text",
            action_kind=ActionKind.WRITE,
            risk_level=RiskLevel.HIGH,
            side_effect_type=EffectKind.UPDATE,
            resource_type="file",
            resource_id_path=("path",),
            expected_change_bindings=(
                ExpectedChangeBinding(
                    output_field="old_text_sha256",
                    argument_path=("old_str",),
                    projection=ArgumentProjection.SHA256,
                ),
                ExpectedChangeBinding(
                    output_field="new_text_sha256",
                    argument_path=("new_str",),
                    projection=ArgumentProjection.SHA256,
                ),
                ExpectedChangeBinding(output_field="replace_all", argument_path=("replace_all",)),
            ),
            idempotency_paths=(("path",), ("old_str",), ("new_str",), ("replace_all",)),
            required_evidence=("current_file_version",),
            dependency_tool_names=("read_file",),
            verification_strategy="file_content_readback",
            idempotency_strategy="canonical_selected_arguments",
        ),
    )


__all__ = ["core_sandbox_tool_profiles"]

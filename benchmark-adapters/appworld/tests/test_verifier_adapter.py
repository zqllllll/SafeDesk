from __future__ import annotations

from datetime import UTC, datetime

from agentgate_appworld import AppWorldEnvironmentVerifier, AppWorldReadbackProfile
from agentgate_core.contracts import EffectKind, EffectRecord, EffectStatus, ResourceRef, VerifierSpec

NOW = datetime(2026, 7, 21, tzinfo=UTC)


def make_effect(resource_id: str | None = "playlist-1") -> EffectRecord:
    return EffectRecord(
        effect_id="effect-1",
        task_id="task-1",
        action_id="action-1",
        idempotency_key="task-1:update:playlist-1",
        kind=EffectKind.UPDATE,
        operation="update",
        resource=ResourceRef(resource_type="spotify_playlist", resource_id=resource_id),
        expected_change={"title": "Focus"},
        actual_change={"ok": True},
        status=EffectStatus.APPLIED_UNVERIFIED,
        created_at=NOW,
        updated_at=NOW,
    )


def test_appworld_verifier_uses_only_configured_public_read_api() -> None:
    calls = []

    def execute(app, api, arguments):
        calls.append((app, api, arguments))
        return {"playlist": {"id": "playlist-1", "title": "Focus"}}

    verifier = AppWorldEnvironmentVerifier(
        execute,
        (
            AppWorldReadbackProfile(
                resource_type="spotify_playlist",
                app_name="spotify",
                api_name="show_playlist",
                resource_id_argument="playlist_id",
                response_path=("playlist",),
            ),
        ),
        id_factory=lambda: "1",
    )
    observation = verifier.observe(
        make_effect(),
        VerifierSpec(
            verifier_name="spotify-readback",
            verifier_version="1",
            resource_types=("spotify_playlist",),
        ),
        1,
    )

    assert calls == [("spotify", "show_playlist", {"playlist_id": "playlist-1"})]
    assert observation.observed_state == {"id": "playlist-1", "title": "Focus"}
    assert observation.error_code is None


def test_appworld_verifier_refuses_readback_without_required_resource_id() -> None:
    verifier = AppWorldEnvironmentVerifier(
        lambda app, api, arguments: {},
        (
            AppWorldReadbackProfile(
                resource_type="spotify_playlist",
                app_name="spotify",
                api_name="show_playlist",
                resource_id_argument="playlist_id",
            ),
        ),
    )

    observation = verifier.observe(
        make_effect(None),
        VerifierSpec(
            verifier_name="spotify-readback",
            verifier_version="1",
            resource_types=("spotify_playlist",),
        ),
        1,
    )

    assert observation.error_code == "resource_id_missing"

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from src.models import Message, MessageRole, Preset, PresetScope, User, UserConfig, UserRole


def test_user_round_trip_and_normalization() -> None:
    user = User(username=" Alice ", email=" Alice@example.com ", role=UserRole.ADMIN)

    payload = user.model_dump()
    restored = User.model_validate(payload)

    assert restored.username == "Alice"
    assert restored.email == "alice@example.com"
    assert restored.role == UserRole.ADMIN
    assert isinstance(UUID(str(restored.id)), UUID)


def test_message_validation_and_serialization() -> None:
    message = Message(
        session_id=UUID(int=1),
        role=MessageRole.USER,
        content="  hello world  ",
        sequence=0,
        metadata={"source": "unit-test"},
    )

    assert message.content == "hello world"
    assert message.model_dump(mode="json")["role"] == "user"


@pytest.mark.parametrize(
    ("scope", "owner_id", "should_raise"),
    [
        (PresetScope.PRIVATE, None, True),
        (PresetScope.SHARED, None, True),
        (PresetScope.GLOBAL, None, False),
    ],
)
def test_preset_scope_owner_rule(scope: PresetScope, owner_id: UUID | None, should_raise: bool) -> None:
    data = {
        "name": "Default preset",
        "prompt_template": "You are a helpful assistant.",
        "model_name": "gpt-4.1",
        "scope": scope,
        "owner_id": owner_id,
    }

    if should_raise:
        with pytest.raises(ValueError, match="owner_id"):
            Preset.model_validate(data)
    else:
        preset = Preset.model_validate(data)
        assert preset.scope == PresetScope.GLOBAL


def test_user_config_defaults_and_time_fields() -> None:
    config = UserConfig(user_id=UUID(int=2), default_model="gpt-4.1")

    assert config.theme == "system"
    assert config.language == "zh-CN"
    assert config.created_at.tzinfo is not None
    assert config.updated_at.tzinfo is not None
    assert config.created_at.tzinfo.utcoffset(config.created_at) == timezone.utc.utcoffset(
        config.created_at
    )

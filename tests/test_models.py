"""Tests for domain models — frozen dataclasses and state transitions."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.models import (
    Channel,
    GpioConfig,
    RadioState,
    StreamBufferConfig,
    SwitchPosition,
)


class TestChannel:
    def test_channel_is_frozen(self, tmp_path: Path) -> None:
        ch = Channel(
            index=0,
            name="Test",
            stream_url="http://example.com",
            announcement_file=tmp_path / "test.mp3",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            ch.name = "Other"  # type: ignore[misc]


class TestRadioState:
    @pytest.fixture
    def initial_state(self) -> RadioState:
        return RadioState(
            selected_channel_index=0,
            switch_position=SwitchPosition.OFF,
        )

    def test_with_channel_returns_new_state(self, initial_state: RadioState) -> None:
        new_state = initial_state.with_channel(2)
        assert new_state.selected_channel_index == 2
        # Original unchanged
        assert initial_state.selected_channel_index == 0

    def test_with_channel_preserves_other_fields(
        self, initial_state: RadioState
    ) -> None:
        new_state = initial_state.with_channel(3)
        assert new_state.switch_position == initial_state.switch_position

    def test_with_switch_returns_new_state(self, initial_state: RadioState) -> None:
        new_state = initial_state.with_switch(SwitchPosition.ON)
        assert new_state.switch_position == SwitchPosition.ON
        assert initial_state.switch_position == SwitchPosition.OFF

    def test_with_switch_preserves_other_fields(
        self, initial_state: RadioState
    ) -> None:
        new_state = initial_state.with_switch(SwitchPosition.ON)
        assert new_state.selected_channel_index == initial_state.selected_channel_index

    def test_state_is_frozen(self, initial_state: RadioState) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            initial_state.selected_channel_index = 5  # type: ignore[misc]

    def test_chained_transitions(self) -> None:
        state = RadioState(
            selected_channel_index=0,
            switch_position=SwitchPosition.OFF,
        )
        final = state.with_switch(SwitchPosition.ON).with_channel(2)
        assert final.switch_position == SwitchPosition.ON
        assert final.selected_channel_index == 2
        # Original untouched
        assert state.selected_channel_index == 0


class TestGpioConfig:
    def test_frozen(self) -> None:
        config = GpioConfig(
            channel_pins=(17, 22),
            switch_pin=27,
            debounce_ms=200,
            invert_switch=False,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.switch_pin = 99  # type: ignore[misc]


class TestStreamBufferConfig:
    def test_frozen(self) -> None:
        config = StreamBufferConfig(
            enabled=True,
            cache_seconds=8.0,
            demuxer_max_bytes="32MiB",
            network_timeout_seconds=10.0,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.enabled = False  # type: ignore[misc]

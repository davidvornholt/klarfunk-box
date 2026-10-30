"""Tests for RadioController state machine with all dependencies mocked."""

from __future__ import annotations

import time
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock

import pytest

from src.controller import RadioController
from src.models import RadioState, SwitchPosition
from tests.conftest import make_app_config


def wait_for_worker(controller: RadioController) -> None:
    worker = controller._worker_thread
    assert worker is not None
    worker.join(timeout=2.0)
    assert not worker.is_alive()


@pytest.fixture
def controller_deps(tmp_audio_files: dict[str, Path]) -> dict[str, object]:
    """Create controller with all mocked dependencies."""
    config = make_app_config(tmp_audio_files["audio_dir"])

    audio = MagicMock()
    audio.play_announcement.return_value = True
    audio.play_stream.return_value = True
    audio.play_announcement_with_stream_preload.return_value = True
    audio.is_playing.return_value = False
    audio.is_reconnecting.return_value = False

    network = MagicMock()

    controller = RadioController(
        config=config,
        audio_player=audio,
        network_manager=network,
    )

    return {
        "controller": controller,
        "config": config,
        "audio": audio,
        "network": network,
    }


class TestInitialState:
    def test_default_state(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        state = controller.state
        assert state.selected_channel_index == 0
        assert state.switch_position == SwitchPosition.OFF


class TestHandleStartup:
    def test_startup_switch_on_dispatches_playback(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        audio = controller_deps["audio"]

        controller.handle_startup(SwitchPosition.ON)
        # Give the worker thread time to execute
        time.sleep(0.3)

        assert controller.state.switch_position == SwitchPosition.ON
        audio.play_announcement_with_stream_preload.assert_called()

    def test_startup_switch_off_plays_selector_off(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        audio = controller_deps["audio"]

        controller.handle_startup(SwitchPosition.OFF)

        assert controller.state.switch_position == SwitchPosition.OFF
        audio.play_selector_off_announcement.assert_called_once()
        audio.play_announcement_with_stream_preload.assert_not_called()


class TestHandleChannelButton:
    def test_switches_to_other_channel_while_playing(
        self, controller_deps: dict
    ) -> None:
        controller = controller_deps["controller"]
        audio = controller_deps["audio"]
        channel = controller_deps["config"].channels[1]
        audio.is_playing.return_value = True
        controller.handle_switch_change(SwitchPosition.ON)
        wait_for_worker(controller)
        audio.play_announcement_with_stream_preload.reset_mock()

        controller.handle_channel_button(1)
        wait_for_worker(controller)

        assert controller.state.selected_channel_index == 1
        audio.play_announcement_with_stream_preload.assert_called_once_with(
            channel.announcement_file, channel.stream_url
        )

    def test_ignored_when_switch_off(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        audio = controller_deps["audio"]

        # Switch is OFF by default
        controller.handle_channel_button(0)

        assert not audio.play_announcement_with_stream_preload.called

    @pytest.mark.parametrize("live_check", ["is_playing", "is_reconnecting"])
    def test_ignored_while_selected_channel_plays(
        self, controller_deps: dict, live_check: str
    ) -> None:
        controller = controller_deps["controller"]
        audio = controller_deps["audio"]
        getattr(audio, live_check).return_value = True
        controller.handle_switch_change(SwitchPosition.ON)
        wait_for_worker(controller)
        audio.stop.reset_mock()
        audio.play_announcement_with_stream_preload.reset_mock()

        controller.handle_channel_button(0)

        audio.stop.assert_not_called()
        audio.play_announcement_with_stream_preload.assert_not_called()

    def test_ignored_while_selected_channel_starts(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        audio = controller_deps["audio"]
        announcing = Event()
        connected = Event()

        def announce_and_connect(*_args: object) -> bool:
            announcing.set()
            return connected.wait(timeout=5.0)

        audio.play_announcement_with_stream_preload.side_effect = announce_and_connect
        controller.handle_switch_change(SwitchPosition.ON)
        assert announcing.wait(timeout=2.0)
        audio.stop.reset_mock()

        # A second press during the channel announcement must not restart it
        controller.handle_channel_button(0)
        connected.set()
        wait_for_worker(controller)

        audio.stop.assert_not_called()
        audio.play_announcement_with_stream_preload.assert_called_once()

    def test_restarts_selected_channel_after_stream_died(
        self, controller_deps: dict
    ) -> None:
        controller = controller_deps["controller"]
        audio = controller_deps["audio"]
        channel = controller_deps["config"].channels[0]
        audio.is_playing.return_value = True
        controller.handle_switch_change(SwitchPosition.ON)
        wait_for_worker(controller)
        audio.play_announcement_with_stream_preload.reset_mock()

        # The stream dies later, for example after the network dropped
        audio.is_playing.return_value = False
        controller.handle_channel_button(0)
        wait_for_worker(controller)

        assert controller.state.selected_channel_index == 0
        audio.play_announcement_with_stream_preload.assert_called_once_with(
            channel.announcement_file, channel.stream_url
        )

    def test_invalid_index_no_crash(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        audio = controller_deps["audio"]

        # Set switch to ON
        controller._state = RadioState(
            selected_channel_index=0,
            switch_position=SwitchPosition.ON,
        )

        controller.handle_channel_button(99)
        audio.play_announcement_with_stream_preload.assert_not_called()


class TestHandleSwitchChange:
    def test_switch_on_dispatches_playback(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        audio = controller_deps["audio"]

        controller.handle_switch_change(SwitchPosition.ON)
        time.sleep(0.3)

        assert controller.state.switch_position == SwitchPosition.ON
        audio.play_announcement_with_stream_preload.assert_called()

    def test_switch_off_stops_and_plays_goodbye(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        audio = controller_deps["audio"]

        # Set switch ON first
        controller._state = RadioState(
            selected_channel_index=0,
            switch_position=SwitchPosition.ON,
        )

        controller.handle_switch_change(SwitchPosition.OFF)
        time.sleep(0.3)

        assert controller.state.switch_position == SwitchPosition.OFF
        audio.play_goodbye_announcement.assert_called()

    def test_same_position_ignored(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        audio = controller_deps["audio"]

        # Already OFF
        controller.handle_switch_change(SwitchPosition.OFF)

        audio.play_selector_off_announcement.assert_not_called()
        audio.play_goodbye_announcement.assert_not_called()
        audio.play_announcement_with_stream_preload.assert_not_called()


class TestHandleShutdownRequest:
    def test_stops_audio_and_plays_shutdown(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        audio = controller_deps["audio"]

        controller.handle_shutdown_request()

        audio.stop.assert_called()
        audio.play_shutdown_announcement.assert_called_once()

    def test_sets_cancel_event(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        controller.handle_shutdown_request()
        assert controller._worker_cancel.is_set()


class TestShutdown:
    def test_sets_cancel_and_cleans_up(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        audio = controller_deps["audio"]

        controller.shutdown()

        assert controller._worker_cancel.is_set()
        audio.stop.assert_called()
        audio.cleanup.assert_called_once()

    def test_joins_worker_thread(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        task_started = Event()

        def slow_task() -> None:
            task_started.set()
            time.sleep(0.5)

        controller._dispatch(slow_task)
        assert task_started.wait(timeout=2.0)

        controller.shutdown()

        # shutdown() waits for the running task instead of leaving it behind
        assert controller._worker_thread is not None
        assert not controller._worker_thread.is_alive()


class TestDispatch:
    def test_cancels_previous_operation(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]

        task1_started = Event()
        task1_cancelled = Event()

        def slow_task() -> None:
            task1_started.set()
            if controller._worker_cancel.wait(timeout=5.0):
                task1_cancelled.set()

        controller._dispatch(slow_task)
        assert task1_started.wait(timeout=2.0)

        # Dispatching a second task signals the first one to stop
        controller._dispatch(lambda: None)

        assert task1_cancelled.is_set()

    def test_new_task_executes(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]

        result = Event()
        controller._dispatch(result.set)
        result.wait(timeout=2.0)

        assert result.is_set()


class TestGetChannel:
    def test_valid_index(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        channel = controller._get_channel(0)
        assert channel is not None
        assert channel.index == 0

    def test_invalid_index_returns_none(self, controller_deps: dict) -> None:
        controller = controller_deps["controller"]
        assert controller._get_channel(99) is None
        assert controller._get_channel(-1) is None

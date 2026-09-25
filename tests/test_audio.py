"""Tests for MpvAudioPlayer with fully mocked MPV."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from threading import Thread
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from src.audio import MpvAudioPlayer
from src.models import (
    ErrorAnnouncementsConfig,
    RetryConfig,
    StreamWatchdogConfig,
)
from tests.conftest import (
    make_audio_config,
    make_retry_config,
    make_watchdog_config,
)

# Type alias for the make_player factory fixture
type MakePlayerFixture = Callable[..., MpvAudioPlayer]


@pytest.fixture
def audio_files(tmp_path: Path) -> dict[str, Path]:
    """Create temp audio files for announcements."""
    files = {
        "retrying": tmp_path / "error_retrying.mp3",
        "failed": tmp_path / "error_failed.mp3",
        "no_internet": tmp_path / "error_no_internet.mp3",
        "goodbye": tmp_path / "goodbye.mp3",
        "selector_off": tmp_path / "selector_off.mp3",
        "shutdown": tmp_path / "shutdown.mp3",
        "channel_1": tmp_path / "channel_1.mp3",
    }
    for p in files.values():
        p.write_bytes(b"\x00" * 10)
    return files


@pytest.fixture
def make_player(audio_files: dict[str, Path]) -> MakePlayerFixture:
    """Factory to create MpvAudioPlayer with configurable retry/watchdog."""

    def _make(
        retry_config: RetryConfig | None = None,
        watchdog_config: StreamWatchdogConfig | None = None,
    ) -> MpvAudioPlayer:
        return MpvAudioPlayer(
            audio_config=make_audio_config(),
            retry_config=retry_config
            or make_retry_config(max_attempts=2, delay_seconds=0.01),
            watchdog_config=watchdog_config or make_watchdog_config(enabled=False),
            error_announcements=ErrorAnnouncementsConfig(
                retrying=audio_files["retrying"],
                failed=audio_files["failed"],
                no_internet=audio_files["no_internet"],
            ),
            goodbye_announcement=audio_files["goodbye"],
            selector_off_announcement=audio_files["selector_off"],
            shutdown_announcement=audio_files["shutdown"],
        )

    return _make


class TestPlayAnnouncement:
    def test_file_not_found_returns_false(self, make_player: MakePlayerFixture) -> None:
        player = make_player()
        result = player.play_announcement(Path("/nonexistent/file.mp3"))
        assert result is False

    def test_success(
        self, make_player: MakePlayerFixture, mocker: MockerFixture
    ) -> None:
        """Mock MPV to immediately fire end-file event."""
        mock_mpv_class = MagicMock()
        mock_instance = MagicMock()
        mock_mpv_class.return_value = mock_instance

        # Capture the event_callback decorator
        end_file_callback = None

        def fake_event_callback(event_name: str) -> Callable[..., object]:
            def decorator(func: Callable[..., object]) -> Callable[..., object]:
                nonlocal end_file_callback
                if event_name == "end-file":
                    end_file_callback = func
                return func

            return decorator

        mock_instance.event_callback = fake_event_callback
        mock_instance.playback_time = None

        mocker.patch("src.audio.mpv.MPV", mock_mpv_class)

        player = make_player()
        audio_file = next(iter(player._error_announcements.__dict__.values()))

        # Run play_announcement in a thread so we can fire the callback
        result_holder = [None]

        def run() -> None:
            result_holder[0] = player.play_announcement(audio_file)

        t = Thread(target=run)
        t.start()

        # Give it time to set up the callback
        time.sleep(0.1)
        if end_file_callback:
            end_file_callback(MagicMock())

        t.join(timeout=5.0)
        assert result_holder[0] is True
        mock_instance.terminate.assert_called()

    def test_mpv_error_returns_false_and_terminates(
        self, make_player: MakePlayerFixture, mocker: MockerFixture
    ) -> None:
        mock_mpv_class = MagicMock()
        mock_instance = MagicMock()
        mock_mpv_class.return_value = mock_instance
        mock_instance.event_callback = MagicMock(return_value=lambda f: f)
        mock_instance.play.side_effect = RuntimeError("MPV crash")

        mocker.patch("src.audio.mpv.MPV", mock_mpv_class)

        player = make_player()
        result = player.play_announcement(player._error_announcements.retrying)
        assert result is False
        mock_instance.terminate.assert_called()


class TestPlayStream:
    def test_cancel_during_connect(
        self, make_player: MakePlayerFixture, mocker: MockerFixture
    ) -> None:
        """Cancelling via stop() while stream is connecting should abort."""
        mock_mpv_class = MagicMock()
        mock_instance = MagicMock()
        mock_mpv_class.return_value = mock_instance

        player = make_player()

        # When play() is called, simulate cancellation from another thread
        def set_cancel_on_play(*_args: object) -> None:
            player._cancel.set()

        mock_instance.play.side_effect = set_cancel_on_play
        mock_instance.playback_time = None

        mocker.patch("src.audio.mpv.MPV", mock_mpv_class)

        result = player.play_stream("http://example.com/stream")
        assert result is False


class TestStopInternal:
    def test_clear_desired_true(self, make_player: MakePlayerFixture) -> None:
        player = make_player()
        player._desired_stream_url = "http://example.com"
        player._current_stream_url = "http://example.com"
        player._is_stream_active = True

        player._stop_internal(clear_desired=True)

        assert player._desired_stream_url is None
        assert player._current_stream_url is None
        assert player._is_stream_active is False

    def test_clear_desired_false_preserves_url(
        self, make_player: MakePlayerFixture
    ) -> None:
        player = make_player()
        player._desired_stream_url = "http://example.com"
        player._current_stream_url = "http://example.com"
        player._is_stream_active = True

        player._stop_internal(clear_desired=False)

        assert player._desired_stream_url == "http://example.com"
        assert player._current_stream_url is None
        assert player._is_stream_active is False

    def test_terminates_existing_player(self, make_player: MakePlayerFixture) -> None:
        player_inst = make_player()
        mock_mpv = MagicMock()
        player_inst._player = mock_mpv

        player_inst._stop_internal()

        mock_mpv.terminate.assert_called()
        assert player_inst._player is None

    def test_no_player_is_safe(self, make_player: MakePlayerFixture) -> None:
        player = make_player()
        player._player = None
        player._stop_internal()  # Should not raise


class TestStop:
    def test_sets_cancel_event(self, make_player: MakePlayerFixture) -> None:
        player = make_player()
        assert not player._cancel.is_set()
        player.stop()
        assert player._cancel.is_set()

    def test_terminates_player(self, make_player: MakePlayerFixture) -> None:
        player = make_player()
        mock_mpv = MagicMock()
        player._player = mock_mpv
        player.stop()
        mock_mpv.terminate.assert_called()


class TestIsPlaying:
    def test_false_when_no_stream(self, make_player: MakePlayerFixture) -> None:
        player = make_player()
        assert player.is_playing() is False

    def test_true_when_active(self, make_player: MakePlayerFixture) -> None:
        player = make_player()
        player._is_stream_active = True
        assert player.is_playing() is True


class TestCleanup:
    def test_cleanup_calls_stop(self, make_player: MakePlayerFixture) -> None:
        player = make_player()
        mock_mpv = MagicMock()
        player._player = mock_mpv
        player.cleanup()
        assert player._cancel.is_set()
        mock_mpv.terminate.assert_called()


class TestBackoff:
    def test_increase_doubles(self, make_player: MakePlayerFixture) -> None:
        player = make_player(
            watchdog_config=make_watchdog_config(reconnect_delay_seconds=2.0)
        )
        player._reconnect_backoff_seconds = 2.0
        player._increase_backoff()
        assert player._reconnect_backoff_seconds == 4.0

    def test_increase_capped_at_60(self, make_player: MakePlayerFixture) -> None:
        player = make_player(
            watchdog_config=make_watchdog_config(reconnect_delay_seconds=2.0)
        )
        player._reconnect_backoff_seconds = 40.0
        player._increase_backoff()
        assert player._reconnect_backoff_seconds == 60.0

    def test_reset_backoff(self, make_player: MakePlayerFixture) -> None:
        player = make_player(
            watchdog_config=make_watchdog_config(reconnect_delay_seconds=5.0)
        )
        player._reconnect_backoff_seconds = 30.0
        player._reset_backoff()
        assert player._reconnect_backoff_seconds == 5.0


class TestConvenienceAnnouncements:
    """Test that convenience methods delegate to play_announcement."""

    def test_play_goodbye(
        self, make_player: MakePlayerFixture, mocker: MockerFixture
    ) -> None:
        player = make_player()
        mock_play = mocker.patch.object(player, "play_announcement", return_value=True)
        player.play_goodbye_announcement()
        mock_play.assert_called_once()

    def test_play_selector_off(
        self, make_player: MakePlayerFixture, mocker: MockerFixture
    ) -> None:
        player = make_player()
        mock_play = mocker.patch.object(player, "play_announcement", return_value=True)
        player.play_selector_off_announcement()
        mock_play.assert_called_once()

    def test_play_shutdown(
        self, make_player: MakePlayerFixture, mocker: MockerFixture
    ) -> None:
        player = make_player()
        mock_play = mocker.patch.object(player, "play_announcement", return_value=True)
        player.play_shutdown_announcement()
        mock_play.assert_called_once()

    def test_play_retrying(
        self, make_player: MakePlayerFixture, mocker: MockerFixture
    ) -> None:
        player = make_player()
        mock_play = mocker.patch.object(player, "play_announcement", return_value=True)
        player.play_retrying_announcement()
        mock_play.assert_called_once_with(player._error_announcements.retrying)

    def test_play_failed(
        self, make_player: MakePlayerFixture, mocker: MockerFixture
    ) -> None:
        player = make_player()
        mock_play = mocker.patch.object(player, "play_announcement", return_value=True)
        player.play_failed_announcement()
        mock_play.assert_called_once_with(player._error_announcements.failed)

    def test_play_no_internet(
        self, make_player: MakePlayerFixture, mocker: MockerFixture
    ) -> None:
        player = make_player()
        mock_play = mocker.patch.object(player, "play_announcement", return_value=True)
        player.play_no_internet_announcement()
        mock_play.assert_called_once_with(player._error_announcements.no_internet)

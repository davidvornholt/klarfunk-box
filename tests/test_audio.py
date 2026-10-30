"""Tests for MpvAudioPlayer with fully mocked MPV."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from threading import Event, Thread
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
    FakeMpvPlayer,
    make_audio_config,
    make_retry_config,
    make_watchdog_config,
)

# Type alias for the make_player factory fixture
type MakePlayerFixture = Callable[..., MpvAudioPlayer]


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Poll a condition until it holds or the timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


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


class TestWatchdogReconnect:
    def test_reconnects_stalled_stream_and_keeps_watching(
        self, make_player: MakePlayerFixture, mocker: MockerFixture
    ) -> None:
        """A stall reconnects on the watchdog thread, which then keeps running."""
        streams: list[FakeMpvPlayer] = []

        def create_stream(**kwargs: object) -> FakeMpvPlayer:
            stream = FakeMpvPlayer(**kwargs)
            streams.append(stream)
            return stream

        mocker.patch("src.audio.mpv.MPV", side_effect=create_stream)
        player = make_player(
            retry_config=make_retry_config(max_attempts=1),
            watchdog_config=make_watchdog_config(
                enabled=True, check_interval_seconds=0.05, stall_seconds=1.0
            ),
        )
        mocker.patch.object(player, "play_announcement", return_value=True)

        assert player.play_stream("http://example.com/stream") is True
        # The first stream reached a late position and then stops progressing.
        streams[0].playback_time = 100.0

        assert wait_until(lambda: len(streams) == 2 and player.is_playing())
        watchdog = player._watchdog_thread
        assert watchdog is not None

        # The reconnected stream starts near zero and keeps progressing, so the
        # watchdog must not treat it as stalled.
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            streams[1].playback_time = (streams[1].playback_time or 0.0) + 0.5
            time.sleep(0.05)
        assert len(streams) == 2
        assert watchdog.is_alive()

        # When the reconnected stream stalls too, the same watchdog reconnects it.
        assert wait_until(lambda: len(streams) == 3 and player.is_playing())
        assert player._watchdog_thread is watchdog
        player.stop()

    def test_reports_reconnecting_until_reconnect_ends(
        self, make_player: MakePlayerFixture, mocker: MockerFixture
    ) -> None:
        """A stall reconnect is reported until its connection attempt has ended."""
        mocker.patch("src.audio.mpv.MPV", side_effect=FakeMpvPlayer)
        player = make_player(
            retry_config=make_retry_config(max_attempts=1),
            watchdog_config=make_watchdog_config(
                enabled=True, check_interval_seconds=0.05, stall_seconds=5.0
            ),
        )
        announcing = Event()
        release_announcement = Event()

        def hold_announcement() -> None:
            announcing.set()
            release_announcement.wait(timeout=5.0)

        mocker.patch.object(
            player, "play_retrying_announcement", side_effect=hold_announcement
        )
        assert player.play_stream("http://example.com/stream") is True
        stream = player._player
        assert isinstance(stream, FakeMpvPlayer)

        stream.simulate_end_file("error")
        assert announcing.wait(timeout=5.0)
        playing_during_announcement = player.is_playing()
        reconnecting_during_announcement = player.is_reconnecting()
        release_announcement.set()

        assert wait_until(lambda: player.is_playing() and not player.is_reconnecting())
        player.stop()
        assert playing_during_announcement is False
        assert reconnecting_during_announcement is True


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


class BlockingReadMpvPlayer(FakeMpvPlayer):
    """Fake mpv player whose playback position read waits for a release."""

    def __init__(self) -> None:
        self.read_started = Event()
        self.release_read = Event()
        self.calls: list[str] = []
        super().__init__()

    @property
    def playback_time(self) -> float | None:
        self.read_started.set()
        self.release_read.wait(timeout=5.0)
        self.calls.append("read finished")
        return self._playback_time

    @playback_time.setter
    def playback_time(self, value: float | None) -> None:
        self._playback_time = value

    def terminate(self) -> None:
        self.calls.append("terminate")
        super().terminate()


class TestIsPlaying:
    def test_false_when_no_stream(self, make_player: MakePlayerFixture) -> None:
        player = make_player()
        assert player.is_playing() is False

    def test_true_when_active(self, make_player: MakePlayerFixture) -> None:
        player = make_player()
        stream = FakeMpvPlayer()
        stream.simulate_stream_playing()
        player._player = stream
        player._is_stream_active = True
        assert player.is_playing() is True

    def test_false_when_connected_stream_ended(
        self, make_player: MakePlayerFixture
    ) -> None:
        player = make_player()
        stream = FakeMpvPlayer()
        stream.simulate_stream_playing()
        player._player = stream
        player._is_stream_active = True

        # mpv reports no playback position once the stream has ended
        stream.playback_time = None

        assert player.is_playing() is False

    def test_reconnect_does_not_terminate_during_read(
        self, make_player: MakePlayerFixture
    ) -> None:
        """The watchdog must not terminate mpv while is_playing() reads from it."""
        player = make_player()
        stream = BlockingReadMpvPlayer()
        stream.simulate_stream_playing()
        player._player = stream
        player._is_stream_active = True

        reader = Thread(target=player.is_playing, daemon=True)
        stopper = Thread(target=player._stop_stream_for_reconnect, daemon=True)
        reader.start()
        assert stream.read_started.wait(timeout=5.0)
        stopper.start()
        stopper.join(timeout=0.2)
        stopper_blocked_during_read = stopper.is_alive()

        stream.release_read.set()
        reader.join(timeout=5.0)
        stopper.join(timeout=5.0)

        assert not reader.is_alive()
        assert not stopper.is_alive()
        assert stream.calls == ["read finished", "terminate"]
        assert stopper_blocked_during_read


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
    """Test that convenience methods play their configured announcement file."""

    @pytest.mark.parametrize(
        ("method_name", "audio_file_key"),
        [
            ("play_goodbye_announcement", "goodbye"),
            ("play_selector_off_announcement", "selector_off"),
            ("play_shutdown_announcement", "shutdown"),
            ("play_retrying_announcement", "retrying"),
            ("play_failed_announcement", "failed"),
            ("play_no_internet_announcement", "no_internet"),
        ],
    )
    def test_plays_configured_file(
        self,
        make_player: MakePlayerFixture,
        mocker: MockerFixture,
        audio_files: dict[str, Path],
        method_name: str,
        audio_file_key: str,
    ) -> None:
        player = make_player()
        mock_play = mocker.patch.object(player, "play_announcement", return_value=True)
        getattr(player, method_name)()
        mock_play.assert_called_once_with(audio_files[audio_file_key])

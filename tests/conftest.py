"""Shared test fixtures and comprehensive mocks for Klarfunk Box tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock

import pytest

from src.models import (
    AppConfig,
    AudioConfig,
    BootAnnouncementsConfig,
    Channel,
    ErrorAnnouncementsConfig,
    GpioConfig,
    RetryConfig,
    StreamBufferConfig,
    StreamWatchdogConfig,
    WifiConfig,
)

# ---------------------------------------------------------------------------
# Fake GPIO — full in-memory GpioInterface implementation
# ---------------------------------------------------------------------------


@dataclass
class EdgeDetectRegistration:
    """Record of an add_event_detect call."""

    pin: int
    edge: str
    callback: Callable[[int], None]
    bouncetime: int


class FakeGpio:
    """In-memory GPIO implementing GpioInterface for testing."""

    def __init__(self) -> None:
        self.pin_states: dict[int, bool] = {}
        self.setup_calls: list[tuple[int, bool]] = []
        self.edge_detects: list[EdgeDetectRegistration] = []
        self.cleaned_up = False

    def setup_input(self, pin: int, pull_up: bool) -> None:
        self.setup_calls.append((pin, pull_up))
        # Default state: HIGH (pull-up = True = button not pressed)
        self.pin_states[pin] = True

    def read(self, pin: int) -> bool:
        return self.pin_states.get(pin, True)

    def add_event_detect(
        self,
        pin: int,
        edge: str,
        callback: Callable[[int], None],
        bouncetime: int,
    ) -> None:
        self.edge_detects.append(
            EdgeDetectRegistration(pin, edge, callback, bouncetime)
        )

    def cleanup(self) -> None:
        self.cleaned_up = True

    # --- Test helpers ---

    def set_pin(self, pin: int, state: bool) -> None:
        """Set pin state directly."""
        self.pin_states[pin] = state

    def simulate_edge(self, pin: int) -> None:
        """Simulate an edge event by calling registered callbacks for the pin."""
        for reg in self.edge_detects:
            if reg.pin == pin:
                reg.callback(pin)

    def get_edge_detect(self, pin: int) -> EdgeDetectRegistration | None:
        """Get the edge detection registration for a pin."""
        for reg in self.edge_detects:
            if reg.pin == pin:
                return reg
        return None


# ---------------------------------------------------------------------------
# Fake MPV — comprehensive mock of mpv.MPV
# ---------------------------------------------------------------------------


class FakeMpvPlayer:
    """Comprehensive mock of mpv.MPV with controllable behavior."""

    def __init__(self, **_kwargs: object) -> None:
        self.volume: int = 100
        self.playback_time: float | None = None
        self._playing = False
        self._terminated = False
        self._current_url: str | None = None
        self._playlist: list[str] = []
        self._playlist_pos: int = 0
        self._property_observers: dict[str, list[Callable[..., None]]] = {}
        self._event_callbacks: dict[str, list[Callable[..., None]]] = {}

        # Controllable behavior
        self.should_fail_play = False
        self.play_delay_seconds: float = 0.0
        self.end_file_event: Event = Event()

    def play(self, url: str) -> None:
        if self._terminated:
            raise RuntimeError("Player terminated")
        if self.should_fail_play:
            raise RuntimeError("Simulated play failure")
        self._current_url = url
        self._playlist = [url]
        self._playlist_pos = 0
        self._playing = True
        self.playback_time = 0.1

    def playlist_append(self, url: str) -> None:
        if self._terminated:
            raise RuntimeError("Player terminated")
        self._playlist.append(url)

    def playlist_next(self) -> None:
        if self._playlist_pos + 1 < len(self._playlist):
            self._playlist_pos += 1
            self._current_url = self._playlist[self._playlist_pos]
            self._fire_property_observers("playlist-pos", self._playlist_pos)

    def terminate(self) -> None:
        self._terminated = True
        self._playing = False
        self.playback_time = None

    def wait_for_playback(self) -> None:
        self.end_file_event.wait()

    def property_observer(self, prop: str) -> Callable[..., Callable[..., None]]:
        """Decorator for property observers."""

        def decorator(func: Callable[..., None]) -> Callable[..., None]:
            self._property_observers.setdefault(prop, []).append(func)
            return func

        return decorator

    def event_callback(self, event: str) -> Callable[..., Callable[..., None]]:
        """Decorator for event callbacks."""

        def decorator(func: Callable[..., None]) -> Callable[..., None]:
            self._event_callbacks.setdefault(event, []).append(func)
            return func

        return decorator

    # --- Test helpers ---

    def _fire_property_observers(self, prop: str, value: object) -> None:
        """Fire registered property observers."""
        for cb in self._property_observers.get(prop, []):
            cb(prop, value)

    def _fire_event(self, event: str, event_data: object = None) -> None:
        """Fire registered event callbacks."""
        for cb in self._event_callbacks.get(event, []):
            cb(event_data)

    def simulate_end_file(self, reason: str = "eof") -> None:
        """Simulate an end-file event."""
        mock_event = MagicMock()
        mock_event.reason = reason
        self._fire_event("end-file", mock_event)
        self.end_file_event.set()

    def simulate_stream_playing(self, playback_time: float = 1.0) -> None:
        """Simulate stream successfully playing."""
        self._playing = True
        self.playback_time = playback_time

    def simulate_playlist_transition(self, pos: int = 1) -> None:
        """Simulate playlist transitioning to next item."""
        self._playlist_pos = pos
        self._fire_property_observers("playlist-pos", pos)


# ---------------------------------------------------------------------------
# Audio file helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_audio_files(tmp_path: Path) -> dict[str, Path]:
    """Create empty placeholder audio files in a temp directory."""
    files = {
        "channel_1": "channel_1.mp3",
        "channel_2": "channel_2.mp3",
        "channel_3": "channel_3.mp3",
        "startup_branding": "startup_branding.mp3",
        "goodbye": "goodbye.mp3",
        "selector_off": "selector_off.mp3",
        "shutdown": "shutdown.mp3",
        "boot_connected": "boot_connected.mp3",
        "boot_no_internet": "boot_no_internet.mp3",
        "error_retrying": "error_retrying.mp3",
        "error_failed": "error_failed.mp3",
        "error_no_internet": "error_no_internet.mp3",
    }
    result: dict[str, Path] = {}
    for key, filename in files.items():
        p = tmp_path / filename
        p.write_bytes(b"\x00" * 10)  # Minimal non-empty file
        result[key] = p
    result["audio_dir"] = tmp_path
    return result


# ---------------------------------------------------------------------------
# Config factories
# ---------------------------------------------------------------------------


def make_stream_buffer_config() -> StreamBufferConfig:
    return StreamBufferConfig(
        enabled=True,
        cache_seconds=8.0,
        demuxer_max_bytes="32MiB",
        network_timeout_seconds=10.0,
    )


def make_audio_config() -> AudioConfig:
    return AudioConfig(
        backend="pipewire",
        device="default",
        volume=80,
        buffer=make_stream_buffer_config(),
    )


def make_gpio_config() -> GpioConfig:
    return GpioConfig(
        channel_pins=(17, 22, 23, 24, 25),
        switch_pin=27,
        debounce_ms=200,
        invert_switch=False,
    )


def make_retry_config(
    max_attempts: int = 3,
    delay_seconds: float = 0.01,
) -> RetryConfig:
    return RetryConfig(
        max_attempts=max_attempts,
        delay_seconds=delay_seconds,
    )


def make_watchdog_config(
    enabled: bool = False,
    check_interval_seconds: float = 0.1,
    stall_seconds: float = 2.0,
    reconnect_delay_seconds: float = 0.01,
) -> StreamWatchdogConfig:
    return StreamWatchdogConfig(
        enabled=enabled,
        check_interval_seconds=check_interval_seconds,
        stall_seconds=stall_seconds,
        reconnect_delay_seconds=reconnect_delay_seconds,
        internet_check_enabled=False,
        internet_check_hosts=("1.1.1.1",),
        internet_check_port=53,
        internet_check_timeout_seconds=1.0,
    )


def make_wifi_config() -> WifiConfig:
    return WifiConfig(
        nmcli_path="nmcli",
        command_timeout_seconds=5.0,
    )


def make_channels(audio_dir: Path) -> tuple[Channel, ...]:
    return (
        Channel(
            index=0,
            name="Channel 1",
            stream_url="http://stream1.example.com/play.mp3",
            announcement_file=audio_dir / "channel_1.mp3",
        ),
        Channel(
            index=1,
            name="Channel 2",
            stream_url="http://stream2.example.com/play.mp3",
            announcement_file=audio_dir / "channel_2.mp3",
        ),
        Channel(
            index=2,
            name="Channel 3",
            stream_url="http://stream3.example.com/play.mp3",
            announcement_file=audio_dir / "channel_3.mp3",
        ),
    )


def make_app_config(audio_dir: Path) -> AppConfig:
    channels = make_channels(audio_dir)
    return AppConfig(
        audio=make_audio_config(),
        gpio=make_gpio_config(),
        retry=make_retry_config(),
        watchdog=make_watchdog_config(),
        wifi=make_wifi_config(),
        channels=channels,
        default_channel_index=0,
        audio_dir=audio_dir,
        error_announcements=ErrorAnnouncementsConfig(
            retrying=audio_dir / "error_retrying.mp3",
            failed=audio_dir / "error_failed.mp3",
            no_internet=audio_dir / "error_no_internet.mp3",
        ),
        boot_announcements=BootAnnouncementsConfig(
            connected=audio_dir / "boot_connected.mp3",
            no_internet=audio_dir / "boot_no_internet.mp3",
        ),
        startup_branding_announcement=audio_dir / "startup_branding.mp3",
        goodbye_announcement=audio_dir / "goodbye.mp3",
        selector_off_announcement=audio_dir / "selector_off.mp3",
        shutdown_announcement=audio_dir / "shutdown.mp3",
    )

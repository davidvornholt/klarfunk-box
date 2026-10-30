"""Main application controller for Klarfunk Box.

Implements the state machine for radio behavior with a worker thread
for non-blocking audio operations.
"""

import logging
from collections.abc import Callable
from functools import partial
from threading import Event, Lock, Thread
from typing import Protocol

from .audio import AudioPlayer
from .models import AppConfig, Channel, RadioState, SwitchPosition
from .network import NetworkManager

logger = logging.getLogger(__name__)


class ShutdownRequester(Protocol):
    """Protocol for requesting application shutdown."""

    def request_shutdown(self) -> None:
        """Request graceful application shutdown."""
        ...


class RadioController:
    """Main controller implementing radio state machine.

    All long-running audio operations are dispatched to a background
    worker thread so GPIO callbacks return immediately.
    """

    def __init__(
        self,
        config: AppConfig,
        audio_player: AudioPlayer,
        network_manager: NetworkManager,
    ) -> None:
        """Initialize radio controller.

        Args:
            config: Application configuration.
            audio_player: Audio player instance.
            network_manager: Network management instance.
        """
        self._config = config
        self._audio = audio_player
        self._network = network_manager
        self._lock = Lock()
        self._state = RadioState(
            selected_channel_index=config.default_channel_index,
            switch_position=SwitchPosition.OFF,
        )
        self._worker_thread: Thread | None = None
        self._worker_cancel = Event()

    @property
    def state(self) -> RadioState:
        """Get current radio state."""
        return self._state

    def _get_channel(self, index: int) -> Channel | None:
        """Get channel by index.

        Args:
            index: Channel index.

        Returns:
            Channel if found, None otherwise.
        """
        if 0 <= index < len(self._config.channels):
            return self._config.channels[index]
        return None

    def _is_channel_running(self) -> bool:
        """Check whether the selected channel is starting, playing, or reconnecting."""
        worker = self._worker_thread
        is_starting = worker is not None and worker.is_alive()
        return is_starting or self._audio.is_playing() or self._audio.is_reconnecting()

    def _dispatch(self, task: Callable[[], None]) -> None:
        """Cancel current work and dispatch a new audio task to the worker.

        Args:
            task: Callable to run on the worker thread.
        """
        # Signal current operation to stop
        self._worker_cancel.set()
        self._audio.stop()

        # Wait briefly for previous worker to notice cancellation
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)

        self._worker_cancel.clear()
        self._worker_thread = Thread(target=task, daemon=True, name="radio-worker")
        self._worker_thread.start()

    def _play_channel_task(self, channel: Channel) -> None:
        """Worker task: announce and start playing a channel.

        Args:
            channel: Channel to play.
        """
        logger.info("Announcing channel: %s", channel.name)

        success = self._audio.play_announcement_with_stream_preload(
            channel.announcement_file,
            channel.stream_url,
        )

        if self._worker_cancel.is_set():
            return

        if not success:
            logger.error("Failed to start stream for channel: %s", channel.name)
            self._audio.play_failed_announcement()

    def handle_startup(self, initial_switch_position: SwitchPosition) -> None:
        """Handle application startup.

        Args:
            initial_switch_position: Initial position of the selector switch.
        """
        with self._lock:
            self._state = self._state.with_switch(initial_switch_position)

        self._announce_boot_connectivity()

        with self._lock:
            position = self._state.switch_position
            channel_index = self._state.selected_channel_index

        match position:
            case SwitchPosition.ON:
                channel = self._get_channel(channel_index)
                if channel is not None:
                    logger.info("Startup with switch ON, playing default channel")
                    self._dispatch(partial(self._play_channel_task, channel))

            case SwitchPosition.OFF:
                logger.info("Startup with switch OFF, playing info announcement")
                self._audio.play_selector_off_announcement()

    def handle_channel_button(self, channel_index: int) -> None:
        """Handle channel button press.

        Args:
            channel_index: Index of the channel button pressed.
        """
        with self._lock:
            # Ignore if switch is OFF
            if self._state.switch_position == SwitchPosition.OFF:
                logger.debug("Ignoring channel button - switch is OFF")
                return

            # Ignore a repeated press so it does not interrupt the radio, but
            # restart the channel when its stream has died
            if (
                self._state.selected_channel_index == channel_index
                and self._is_channel_running()
            ):
                logger.debug("Ignoring channel button - channel is running")
                return

            channel = self._get_channel(channel_index)
            if channel is None:
                logger.warning("Invalid channel index: %d", channel_index)
                return

            # Update selected channel immediately
            self._state = self._state.with_channel(channel_index)

        # Dispatch long audio operation to worker thread (outside lock)
        logger.info(
            "Channel %d button pressed, dispatching playback", channel_index + 1
        )
        self._dispatch(partial(self._play_channel_task, channel))

    def handle_switch_change(self, position: SwitchPosition) -> None:
        """Handle selector switch position change.

        Args:
            position: New switch position.
        """
        with self._lock:
            previous_position = self._state.switch_position
            self._state = self._state.with_switch(position)

            # Ignore if position hasn't actually changed
            if position == previous_position:
                return

            channel_index = self._state.selected_channel_index

        # Dispatch audio operations outside lock
        match position:
            case SwitchPosition.ON:
                channel = self._get_channel(channel_index)
                if channel is not None:
                    logger.info("Switch turned ON, starting playback")
                    self._dispatch(partial(self._play_channel_task, channel))

            case SwitchPosition.OFF:
                logger.info("Switch turned OFF, stopping playback")
                self._dispatch(self._switch_off_task)

    def _switch_off_task(self) -> None:
        """Worker task: stop playback and play goodbye announcement."""
        self._audio.stop()
        self._audio.play_goodbye_announcement()

    def handle_shutdown_request(self) -> None:
        """Handle shutdown request from long-press on channel 1 button.

        Stops current playback and plays shutdown announcement.
        """
        # Cancel any current worker operations
        self._worker_cancel.set()
        self._audio.stop()

        logger.info("Shutdown requested via long-press")
        self._audio.play_shutdown_announcement()

    def shutdown(self) -> None:
        """Perform graceful shutdown."""
        logger.info("Shutting down radio controller")
        self._worker_cancel.set()
        self._audio.stop()

        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)

        self._audio.cleanup()

    def _announce_boot_connectivity(self) -> None:
        status = self._network.check_connectivity()
        announcement = (
            self._config.boot_announcements.connected
            if status.is_connected
            else self._config.boot_announcements.no_internet
        )
        self._audio.play_announcement(announcement)

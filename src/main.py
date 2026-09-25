"""Klarfunk Box - Main entry point.

A simple internet radio application for older adults on Raspberry Pi.
"""

import argparse
import logging
import platform
import resource
import signal
import subprocess
import sys
import time
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Event, Thread
from types import FrameType

from .audio import MpvAudioPlayer
from .config import DEFAULT_CONFIG_PATH, load_config
from .constants import ANNOUNCEMENT_TIMEOUT_SECONDS
from .controller import RadioController
from .gpio import GpioController, RpiGpioAdapter
from .gpio_mock import KeyboardGpioAdapter
from .network import NetworkManager

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 30.0


def setup_logging(verbose: bool, log_file: Path | None = None) -> None:
    """Configure logging with optional rotating file handler.

    Args:
        verbose: Enable debug logging if True.
        log_file: Optional path for a rotating log file.
    """
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if log_file is not None:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )


def log_fd_limits() -> None:
    """Log file descriptor limits for diagnostic purposes."""
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        logger.info("File descriptor limits: soft=%d, hard=%d", soft, hard)
    except (ValueError, OSError) as e:
        logger.warning("Could not read FD limits: %s", e)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    Returns:
        Parsed arguments.
    """
    default_gpio_backend = get_default_gpio_backend()
    parser = argparse.ArgumentParser(
        prog="klarfunk-box",
        description="A simple internet radio for older adults",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to configuration file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--gpio",
        choices=["rpi", "mock"],
        default=default_gpio_backend,
        help="GPIO backend to use (rpi or mock)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Path to rotating log file (5 MB × 3 backups)",
    )
    parser.add_argument(
        "--heartbeat-file",
        type=Path,
        default=None,
        help="Path to heartbeat file (updated every 30s for external monitoring)",
    )
    return parser.parse_args()


def get_default_gpio_backend() -> str:
    """Select the default GPIO backend based on the current platform."""
    if is_raspberry_pi():
        return "rpi"
    return "mock"


@lru_cache(maxsize=1)
def is_raspberry_pi() -> bool:
    """Detect whether the current machine is a Raspberry Pi."""
    if platform.system() != "Linux":
        return False

    model_path = Path("/sys/firmware/devicetree/base/model")
    if not model_path.exists():
        return False

    try:
        model_text = model_path.read_text(
            encoding="utf-8",
            errors="ignore",
        )
    except OSError:
        return False

    return "Raspberry Pi" in model_text


def start_heartbeat_writer(
    heartbeat_file: Path,
    stop_event: Event,
) -> Thread:
    """Start a background thread that writes timestamps to a heartbeat file.

    Args:
        heartbeat_file: Path to the heartbeat file.
        stop_event: Event to signal the thread to stop.

    Returns:
        The started heartbeat thread.
    """

    def _heartbeat_loop() -> None:
        while not stop_event.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                heartbeat_file.write_text(
                    time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    encoding="utf-8",
                )
            except OSError as e:
                logger.warning("Failed to write heartbeat file: %s", e)

    thread = Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
    thread.start()
    logger.info(
        "Heartbeat file: %s (every %.0fs)", heartbeat_file, HEARTBEAT_INTERVAL_SECONDS
    )
    return thread


def start_startup_branding_announcement(
    audio_player: MpvAudioPlayer,
    announcement_file: Path,
) -> Thread | None:
    """Start startup branding announcement playback in the background."""
    if not announcement_file.exists():
        return None

    def _play_startup_branding() -> None:
        logger.info("Playing startup branding announcement")
        success = audio_player.play_announcement(announcement_file)
        if not success:
            logger.warning("Startup branding announcement did not complete")

    thread = Thread(
        target=_play_startup_branding,
        daemon=True,
        name="startup-branding",
    )
    thread.start()
    return thread


def main() -> int:
    """Run the Klarfunk Box application.

    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    args = parse_args()
    setup_logging(args.verbose, log_file=args.log_file)

    logger.info("Starting Klarfunk Box")
    log_fd_limits()

    # Load configuration
    try:
        config = load_config(args.config)
        logger.info("Configuration loaded from %s", args.config)
    except FileNotFoundError:
        logger.exception("Configuration error")
        return 1
    except ValueError:
        logger.exception("Invalid configuration")
        return 1

    # Validate audio files exist
    for channel in config.channels:
        if not channel.announcement_file.exists():
            logger.warning(
                "Announcement file not found for channel '%s': %s",
                channel.name,
                channel.announcement_file,
            )

    # Validate error announcement files
    error_files = [
        ("retrying", config.error_announcements.retrying),
        ("failed", config.error_announcements.failed),
        ("no_internet", config.error_announcements.no_internet),
    ]
    for name, path in error_files:
        if not path.exists():
            logger.warning("Error announcement file '%s' not found: %s", name, path)
    if not config.startup_branding_announcement.exists():
        logger.warning(
            "Startup branding announcement file not found: %s",
            config.startup_branding_announcement,
        )

    # Initialize components
    audio_player = MpvAudioPlayer(
        audio_config=config.audio,
        retry_config=config.retry,
        watchdog_config=config.watchdog,
        error_announcements=config.error_announcements,
        goodbye_announcement=config.goodbye_announcement,
        selector_off_announcement=config.selector_off_announcement,
        shutdown_announcement=config.shutdown_announcement,
    )
    startup_branding_thread = start_startup_branding_announcement(
        audio_player=audio_player,
        announcement_file=config.startup_branding_announcement,
    )

    network_manager = NetworkManager(
        nmcli_path=config.wifi.nmcli_path,
        command_timeout_seconds=config.wifi.command_timeout_seconds,
        connect_timeout_seconds=config.wifi.connect_timeout_seconds,
        internet_check_hosts=config.watchdog.internet_check_hosts,
        internet_check_port=config.watchdog.internet_check_port,
        internet_check_timeout_seconds=config.watchdog.internet_check_timeout_seconds,
    )

    radio_controller = RadioController(
        config=config,
        audio_player=audio_player,
        network_manager=network_manager,
    )

    gpio_mode = args.gpio
    is_pi = is_raspberry_pi()
    if gpio_mode == "rpi" and not is_pi:
        logger.warning("RPi GPIO selected but Raspberry Pi not detected; using mock.")
        gpio_mode = "mock"

    if gpio_mode == "mock":
        gpio = KeyboardGpioAdapter(
            channel_pins=config.gpio.channel_pins,
            switch_pin=config.gpio.switch_pin,
        )
        logger.info(
            "GPIO mock controls: 1-5 = channels, s = toggle switch, hold 1 for shutdown"
        )
    else:
        try:
            gpio = RpiGpioAdapter()
        except (ImportError, RuntimeError, OSError) as e:
            logger.critical("Failed to initialize GPIO: %s", e)
            logger.critical(
                "Ensure RPi.GPIO is installed and /dev/gpiomem is accessible "
                "(try: sudo usermod -aG gpio $USER)"
            )
            return 1

    # Event-based shutdown signalling (works for both signals and mock mode)
    shutdown_event = Event()

    # Define shutdown callback for long-press
    def handle_shutdown_request() -> None:
        radio_controller.handle_shutdown_request()
        shutdown_event.set()
        if gpio_mode == "mock":
            logger.info("Mock GPIO: shutdown requested (no system shutdown)")
            return
        # Execute system shutdown after announcement
        logger.info("Executing system shutdown")
        subprocess.run(["sudo", "shutdown", "-h", "now"], check=False)

    gpio_controller = GpioController(
        config=config.gpio,
        gpio=gpio,
        on_channel_button=radio_controller.handle_channel_button,
        on_switch_change=radio_controller.handle_switch_change,
        on_shutdown_requested=handle_shutdown_request,
    )

    # Setup signal handlers for graceful shutdown
    def handle_signal(signum: int, _frame: FrameType | None) -> None:
        logger.info("Shutdown signal received (%s)", signal.Signals(signum).name)
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Start heartbeat writer if configured
    heartbeat_thread: Thread | None = None
    if args.heartbeat_file is not None:
        heartbeat_thread = start_heartbeat_writer(args.heartbeat_file, shutdown_event)

    # Start GPIO controller
    gpio_controller.start()

    # Keep initialization non-blocking while branding audio plays.
    # Before startup announcements/channels, wait briefly to avoid overlap.
    if startup_branding_thread is not None:
        startup_branding_thread.join(timeout=ANNOUNCEMENT_TIMEOUT_SECONDS)

    # Handle startup based on switch position
    initial_switch_position = gpio_controller.get_switch_position()
    radio_controller.handle_startup(initial_switch_position)

    logger.info("Klarfunk Box is running. Press Ctrl+C to stop.")

    # Main loop — wait for shutdown event (works for signals AND mock mode)
    shutdown_event.wait()

    # Graceful shutdown
    logger.info("Shutting down Klarfunk Box")
    gpio_controller.stop()
    radio_controller.shutdown()

    if heartbeat_thread is not None:
        heartbeat_thread.join(timeout=1.0)

    logger.info("Goodbye!")

    return 0


if __name__ == "__main__":
    sys.exit(main())

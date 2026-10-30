"""Configuration loader for Klarfunk Box.

Loads and validates YAML configuration files using Pydantic.
"""

import re
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .models import (
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

DEFAULT_CONFIG_PATH = Path("config/config.yaml")
DEFAULT_AUDIO_DIR = "audio"

DEFAULT_GPIO_PINS = (17, 22, 23, 24, 25)
DEFAULT_SWITCH_PIN = 27
DEFAULT_DEBOUNCE_MS = 200


class ChannelSchema(BaseModel):
    """Schema for channel configuration."""

    name: str
    stream_url: str
    announcement_file: str


class BufferSchema(BaseModel):
    """Schema for stream buffer configuration."""

    enabled: bool = True
    cache_seconds: float = Field(default=8.0, ge=1.0)
    demuxer_max_bytes: str = "32MiB"
    network_timeout_seconds: float = Field(default=10.0, ge=1.0)

    @field_validator("demuxer_max_bytes")
    @classmethod
    def validate_demuxer_max_bytes(cls, v: str) -> str:
        cleaned = v.strip()
        if not re.fullmatch(
            r"\d+(B|KB|KiB|MB|MiB|GB|GiB)",
            cleaned,
            re.IGNORECASE,
        ):
            msg = "demuxer_max_bytes must be like '32MiB' or '64KB'"
            raise ValueError(msg)
        return cleaned


class AudioSchema(BaseModel):
    """Schema for audio configuration."""

    backend: str = Field(default="alsa", pattern="^(alsa|pipewire)$")
    device: str = "default"
    volume: int = Field(default=80, ge=0, le=100)
    buffer: BufferSchema = Field(default_factory=BufferSchema)


class GpioSchema(BaseModel):
    """Schema for GPIO configuration."""

    channel_pins: list[int] = Field(default_factory=lambda: list(DEFAULT_GPIO_PINS))
    switch_pin: int = DEFAULT_SWITCH_PIN
    debounce_ms: int = DEFAULT_DEBOUNCE_MS
    invert_switch: bool = False


class RetrySchema(BaseModel):
    """Schema for retry configuration."""

    max_attempts: int = Field(default=3, ge=1)
    delay_seconds: float = Field(default=5.0, ge=0.5)


class WatchdogSchema(BaseModel):
    """Schema for stream watchdog configuration."""

    enabled: bool = True
    check_interval_seconds: float = Field(default=2.0, ge=0.5)
    stall_seconds: float = Field(default=8.0, ge=2.0)
    reconnect_delay_seconds: float = Field(default=2.0, ge=0.5)
    internet_check_enabled: bool = True
    internet_check_hosts: list[str] = Field(
        default_factory=lambda: ["1.1.1.1", "8.8.8.8"]
    )
    internet_check_port: int = Field(default=53, ge=1, le=65535)
    internet_check_timeout_seconds: float = Field(default=2.0, ge=0.5)


class WifiSchema(BaseModel):
    """Schema for WiFi management configuration."""

    nmcli_path: str = "nmcli"
    command_timeout_seconds: float = Field(default=5.0, ge=1.0)

class BootAnnouncementsSchema(BaseModel):
    """Schema for boot announcement audio files."""

    connected: str = "boot_connected.mp3"
    no_internet: str = "boot_no_internet.mp3"


class ErrorAnnouncementsSchema(BaseModel):
    """Schema for error announcement files."""

    retrying: str = "error_retrying.mp3"
    failed: str = "error_failed.mp3"
    no_internet: str = "error_no_internet.mp3"


class ConfigSchema(BaseModel):
    """Schema for complete configuration."""

    audio: AudioSchema = Field(default_factory=AudioSchema)
    gpio: GpioSchema = Field(default_factory=GpioSchema)
    retry: RetrySchema = Field(default_factory=RetrySchema)
    watchdog: WatchdogSchema = Field(default_factory=WatchdogSchema)
    wifi: WifiSchema = Field(default_factory=WifiSchema)
    channels: list[ChannelSchema] = Field(min_length=1, max_length=5)
    default_channel: int = Field(default=0, ge=0)
    audio_dir: str = DEFAULT_AUDIO_DIR
    error_announcements: ErrorAnnouncementsSchema = Field(
        default_factory=ErrorAnnouncementsSchema
    )
    boot_announcements: BootAnnouncementsSchema = Field(
        default_factory=BootAnnouncementsSchema
    )
    startup_branding_announcement: str = "startup_branding.mp3"
    goodbye_announcement: str = "goodbye.mp3"
    selector_off_announcement: str = "selector_off.mp3"
    shutdown_announcement: str = "shutdown.mp3"

    @field_validator("channels")
    @classmethod
    def validate_channels_count(cls, v: list[ChannelSchema]) -> list[ChannelSchema]:
        """Ensure we have at least one channel."""
        if len(v) == 0:
            msg = "At least one channel must be configured"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def validate_default_channel(self) -> Self:
        """Ensure default channel index is valid."""
        if self.default_channel >= len(self.channels):
            msg = f"default_channel {self.default_channel} exceeds channel count {len(self.channels)}"
            raise ValueError(msg)
        return self


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load and validate configuration from YAML file.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Validated AppConfig instance.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValueError: If configuration is invalid.
    """
    if not config_path.exists():
        msg = f"Configuration file not found: {config_path}"
        raise FileNotFoundError(msg)

    with config_path.open() as f:
        raw_config = yaml.safe_load(f)

    schema = ConfigSchema.model_validate(raw_config)

    # Resolve audio_dir relative to config file location
    config_dir = config_path.parent.resolve()
    audio_dir_path = Path(schema.audio_dir)
    if not audio_dir_path.is_absolute():
        audio_dir = (config_dir / audio_dir_path).resolve()
    else:
        audio_dir = audio_dir_path

    channels = tuple(
        Channel(
            index=i,
            name=ch.name,
            stream_url=ch.stream_url,
            announcement_file=audio_dir / ch.announcement_file,
        )
        for i, ch in enumerate(schema.channels)
    )

    return AppConfig(
        audio=AudioConfig(
            backend=schema.audio.backend,
            device=schema.audio.device,
            volume=schema.audio.volume,
            buffer=StreamBufferConfig(
                enabled=schema.audio.buffer.enabled,
                cache_seconds=schema.audio.buffer.cache_seconds,
                demuxer_max_bytes=schema.audio.buffer.demuxer_max_bytes,
                network_timeout_seconds=schema.audio.buffer.network_timeout_seconds,
            ),
        ),
        gpio=GpioConfig(
            channel_pins=tuple(schema.gpio.channel_pins),
            switch_pin=schema.gpio.switch_pin,
            debounce_ms=schema.gpio.debounce_ms,
            invert_switch=schema.gpio.invert_switch,
        ),
        retry=RetryConfig(
            max_attempts=schema.retry.max_attempts,
            delay_seconds=schema.retry.delay_seconds,
        ),
        watchdog=StreamWatchdogConfig(
            enabled=schema.watchdog.enabled,
            check_interval_seconds=schema.watchdog.check_interval_seconds,
            stall_seconds=schema.watchdog.stall_seconds,
            reconnect_delay_seconds=schema.watchdog.reconnect_delay_seconds,
            internet_check_enabled=schema.watchdog.internet_check_enabled,
            internet_check_hosts=tuple(schema.watchdog.internet_check_hosts),
            internet_check_port=schema.watchdog.internet_check_port,
            internet_check_timeout_seconds=schema.watchdog.internet_check_timeout_seconds,
        ),
        wifi=WifiConfig(
            nmcli_path=schema.wifi.nmcli_path,
            command_timeout_seconds=schema.wifi.command_timeout_seconds,
        ),
        channels=channels,
        default_channel_index=schema.default_channel,
        audio_dir=audio_dir,
        error_announcements=ErrorAnnouncementsConfig(
            retrying=audio_dir / schema.error_announcements.retrying,
            failed=audio_dir / schema.error_announcements.failed,
            no_internet=audio_dir / schema.error_announcements.no_internet,
        ),
        boot_announcements=BootAnnouncementsConfig(
            connected=audio_dir / schema.boot_announcements.connected,
            no_internet=audio_dir / schema.boot_announcements.no_internet,
        ),
        startup_branding_announcement=audio_dir / schema.startup_branding_announcement,
        goodbye_announcement=audio_dir / schema.goodbye_announcement,
        selector_off_announcement=audio_dir / schema.selector_off_announcement,
        shutdown_announcement=audio_dir / schema.shutdown_announcement,
    )

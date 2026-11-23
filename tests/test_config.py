"""Tests for configuration loading and Pydantic validation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from textwrap import dedent

import pytest

from src.config import load_config

# Type alias for the write_yaml fixture
type WriteYamlFixture = Callable[[str], Path]


@pytest.fixture
def write_yaml(tmp_path: Path) -> WriteYamlFixture:
    """Helper to write a YAML config file and return its path."""

    def _write(content: str) -> Path:
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text(dedent(content), encoding="utf-8")
        # Create audio dir so path resolution works
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir(exist_ok=True)
        return config_file

    return _write


class TestLoadConfig:
    def test_load_minimal_config(self, write_yaml: WriteYamlFixture) -> None:
        """A config with only channels and required audio files should load."""
        path = write_yaml("""\
            audio_dir: "../audio"
            channels:
              - name: "Test"
                stream_url: "http://example.com/stream"
                announcement_file: "ch1.mp3"
        """)
        # Create the referenced audio file
        audio_dir = path.parent.parent / "audio"
        (audio_dir / "ch1.mp3").write_bytes(b"fake")

        config = load_config(path)
        assert len(config.channels) == 1
        assert config.channels[0].name == "Test"
        assert config.default_channel_index == 0

    def test_missing_config_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_no_channels_raises(self, write_yaml: WriteYamlFixture) -> None:
        path = write_yaml("""\
            channels: []
        """)
        with pytest.raises(ValueError):
            load_config(path)

    def test_invalid_default_channel_raises(self, write_yaml: WriteYamlFixture) -> None:
        path = write_yaml("""\
            audio_dir: "../audio"
            default_channel: 5
            channels:
              - name: "Test"
                stream_url: "http://example.com/stream"
                announcement_file: "ch1.mp3"
        """)
        with pytest.raises(ValueError):
            load_config(path)

    def test_invalid_audio_backend_raises(self, write_yaml: WriteYamlFixture) -> None:
        path = write_yaml("""\
            audio:
              backend: "invalid_backend"
            audio_dir: "../audio"
            channels:
              - name: "Test"
                stream_url: "http://example.com/stream"
                announcement_file: "ch1.mp3"
        """)
        with pytest.raises(ValueError):
            load_config(path)

    def test_volume_out_of_range_raises(self, write_yaml: WriteYamlFixture) -> None:
        path = write_yaml("""\
            audio:
              volume: 150
            audio_dir: "../audio"
            channels:
              - name: "Test"
                stream_url: "http://example.com/stream"
                announcement_file: "ch1.mp3"
        """)
        with pytest.raises(ValueError):
            load_config(path)

    def test_demuxer_max_bytes_invalid_raises(
        self, write_yaml: WriteYamlFixture
    ) -> None:
        path = write_yaml("""\
            audio:
              buffer:
                demuxer_max_bytes: "invalid"
            audio_dir: "../audio"
            channels:
              - name: "Test"
                stream_url: "http://example.com/stream"
                announcement_file: "ch1.mp3"
        """)
        with pytest.raises(ValueError):
            load_config(path)

    def test_config_defaults(self, write_yaml: WriteYamlFixture) -> None:
        """Verify default values are applied."""
        path = write_yaml("""\
            audio_dir: "../audio"
            channels:
              - name: "Test"
                stream_url: "http://example.com/stream"
                announcement_file: "ch1.mp3"
        """)
        audio_dir = path.parent.parent / "audio"
        (audio_dir / "ch1.mp3").write_bytes(b"fake")

        config = load_config(path)
        assert config.audio.backend == "alsa"
        assert config.audio.volume == 80
        assert config.retry.max_attempts == 3
        assert config.retry.delay_seconds == 5.0
        assert config.gpio.debounce_ms == 200
        assert config.watchdog.enabled is True
        assert (
            config.startup_branding_announcement == audio_dir / "startup_branding.mp3"
        )

    def test_channel_announcement_path_resolved(
        self, write_yaml: WriteYamlFixture
    ) -> None:
        """Announcement paths should be resolved relative to audio_dir."""
        path = write_yaml("""\
            audio_dir: "../audio"
            channels:
              - name: "Test"
                stream_url: "http://example.com/stream"
                announcement_file: "ch1.mp3"
        """)
        audio_dir = path.parent.parent / "audio"
        (audio_dir / "ch1.mp3").write_bytes(b"fake")

        config = load_config(path)
        assert config.channels[0].announcement_file == audio_dir / "ch1.mp3"

    def test_multiple_channels(self, write_yaml: WriteYamlFixture) -> None:
        path = write_yaml("""\
            audio_dir: "../audio"
            channels:
              - name: "One"
                stream_url: "http://one.example.com/stream"
                announcement_file: "ch1.mp3"
              - name: "Two"
                stream_url: "http://two.example.com/stream"
                announcement_file: "ch2.mp3"
        """)
        audio_dir = path.parent.parent / "audio"
        (audio_dir / "ch1.mp3").write_bytes(b"fake")
        (audio_dir / "ch2.mp3").write_bytes(b"fake")

        config = load_config(path)
        assert len(config.channels) == 2
        assert config.channels[0].name == "One"
        assert config.channels[1].name == "Two"

    def test_load_example_config(self) -> None:
        """The example config in the repo should load without errors."""
        example_path = Path("config/config.example.yaml")
        if not example_path.exists():
            pytest.skip("Example config not found")
        config = load_config(example_path)
        assert len(config.channels) > 0

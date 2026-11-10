# Klarfunk Box

An easy-to-use and intuitive internet radio application for seniors, designed to run on Raspberry Pi with minimal resources (512 MB RAM).

## Features

- **5 configurable radio channels** via physical buttons
- **Simple on/off control** via selector switch
- **Voice announcements** when switching channels
- **Automatic retry** on stream connection failure
- **Safe shutdown** via long-press (5 seconds) on channel 1 button
- **No debug long-press mode** on channel 2
- **Low resource usage** optimized for Raspberry Pi

## Hardware Requirements

- Raspberry Pi (any model with GPIO, 512 MB RAM minimum)
- 5 momentary push buttons (for channel selection)
- 1 selector switch (for start/stop)
- Audio output (3.5mm jack or USB audio device)
- Internet connection

### GPIO Wiring

| Component | GPIO Pin (BCM) |
|-----------|----------------|
| Channel 1 Button | GPIO 17 |
| Channel 2 Button | GPIO 22 |
| Channel 3 Button | GPIO 23 |
| Channel 4 Button | GPIO 24 |
| Channel 5 Button | GPIO 25 |
| Start/Stop Switch | GPIO 27 |

All buttons should be wired between GPIO and GND (internal pull-up resistors are enabled).

## Installation

### Prerequisites

```bash
# Install MPV player and build dependencies
sudo apt-get update
sudo apt-get install -y git mpv libmpv-dev libmpv2 swig liblgpio-dev python3-dev build-essential

# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

> [!NOTE]
> `swig` is required to build the `lgpio` GPIO library from source on Debian Trixie and newer.

```bash
# Add your user to the gpio group (required for GPIO access without root)
sudo usermod -aG gpio $USER
# Log out and back in for this to take effect
```

### Application Setup

```bash
# Clone the repository
git clone https://github.com/davidvornholt/klarfunk-box.git
cd klarfunk-box

# Install dependencies
uv sync

# Copy example configuration
cp config/config.example.yaml config/config.yaml

# Edit configuration
nano config/config.yaml

# Add your announcement audio files to audio/
# See audio/README.md for details
```

### Running

```bash
# Run directly (uses config/config.yaml by default)
uv run python -m src.main

# Or with verbose logging
uv run python -m src.main --verbose

# Or with custom config path
uv run python -m src.main --config /path/to/config.yaml

# Run with keyboard-based GPIO mock (no Raspberry Pi required)
uv run python -m src.main --gpio mock

# Enable rotating log file (5 MB × 3 backups)
uv run python -m src.main --log-file /var/log/klarfunk-box.log

# Enable heartbeat file for external monitoring (updated every 30s)
uv run python -m src.main --heartbeat-file /tmp/klarfunk-box-heartbeat
```

GPIO mock controls:

- Keys `1`-`5`: Channel buttons 1-5
- Key `s`: Toggle selector switch ON/OFF
- Hold key `1`: Trigger shutdown request (mock mode logs only, no system shutdown)
- Channel `2` has no special long-press action

### Running as a Service

Choose the service type based on your audio backend:

- **ALSA** → system service (simpler setup)
- **PipeWire** → user service (required because PipeWire runs per-user)

#### Option A: System Service (ALSA)

Run these commands from inside the repository directory:

```bash
# Create the service file (auto-detects user and directory)
sudo tee /etc/systemd/system/klarfunk-box.service > /dev/null <<EOF
[Unit]
Description=Klarfunk Box Internet Radio
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
ExecStart=$HOME/.local/bin/uv run python -m src.main
Restart=always
RestartSec=10
Environment="HOME=$HOME"

[Install]
WantedBy=multi-user.target
EOF

# Enable and start the service
sudo systemctl daemon-reload
sudo systemctl enable klarfunk-box
sudo systemctl start klarfunk-box
```

```bash
# Check status
sudo systemctl status klarfunk-box

# View logs (follow mode)
journalctl -u klarfunk-box -f
```

#### Option B: User Service (PipeWire)

PipeWire runs as a per-user session daemon. A system service cannot access the user's PipeWire socket, so you **must** use a `systemd --user` service instead.

```bash
# Enable lingering so your user session starts at boot (headless)
sudo loginctl enable-linger $USER

# Create the user service directory
mkdir -p ~/.config/systemd/user
```

```bash
# Create the user service file (run from inside the repository directory)
tee ~/.config/systemd/user/klarfunk-box.service > /dev/null <<EOF
[Unit]
Description=Klarfunk Box Internet Radio
After=network.target pipewire.service wireplumber.service
Wants=pipewire.service wireplumber.service

[Service]
Type=simple
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/.venv/bin/python -m src.main --log-file $(pwd)/klarfunk-box.log --heartbeat-file /tmp/klarfunk-box-heartbeat
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

# Enable and start the service
systemctl --user daemon-reload
systemctl --user enable klarfunk-box
systemctl --user start klarfunk-box
```

```bash
# Check status
systemctl --user status klarfunk-box

# View logs (follow mode)
journalctl --user -u klarfunk-box -f
```

## Configuration

Edit `config/config.yaml` to customize:

- **Audio backend**: `alsa` or `pipewire`
- **Audio device**: 3.5mm jack (`default`) or USB audio (`hw:1,0` / `plughw:1,0`)
- **Volume level**: 0-100
- **Radio channels**: Name, stream URL, announcement file
- **Retry settings**: Connection attempts and delay
- **Buffer settings**: Cache seconds and max buffer size; increase for high-latency or bursty networks, decrease for memory-constrained devices.
- **Watchdog settings**: Stream health checks and reconnect timing; tighten for unreliable networks and relax for stable, low-latency connections.
- **audio_dir**: Path to audio files (relative to config file, or absolute)
- **startup_branding_announcement**: Startup audio file with product name and slogan
- **invert_switch**: Swap ON/OFF positions of selector switch (useful if wiring causes opposite behavior)

See `config/config.example.yaml` for a complete example.

## Audio Files

Place your announcement audio files in the `audio/` directory:

- `channel_1.mp3` through `channel_5.mp3`: Channel announcements. Example: "Now playing SWR1 Baden-Württemberg. Have a lovely time listening."
- `error_retrying.mp3`: Played when retrying connection. Example: "Please wait a moment. The radio station connection is being restored."
- `error_failed.mp3`: Played when all retries failed. Example: "Connection is currently unavailable. Please try again in a little while."
- `error_no_internet.mp3`: Played when no internet connection. Example: "There is currently no internet connection. Please check Wi-Fi and try again."
- `startup_branding.mp3`: Played immediately on app start (while app continues initialization). Example: "Welcome to Klarfunk Box - simple radio, made for you."
- `goodbye.mp3`: Played when radio is turned off. Example: "Goodbye for now, and thank you for listening."
- `selector_off.mp3`: Played when radio starts with selector switch off. Example: "Welcome. The radio is ready. Please switch to ON when you want to listen."
- `shutdown.mp3`: Played before system shutdown (triggered by holding channel 1 for 5 seconds). Example: "The device will now shut down safely. See you soon."

All announcement audio files use the same timeout: **15 seconds**. If playback does not complete in time, the app continues with the next startup/playback step.

## Troubleshooting

### No audio output

1. Check volume: `alsamixer`
2. List audio devices: `aplay -l`
3. Test audio: `speaker-test -t wav -c 2`
4. Update config with correct device name

### Stream not connecting

1. Check internet connection: `ping google.de`
2. Test stream URL: `mpv --no-video "STREAM_URL"`
3. Check logs: `journalctl -u klarfunk-box -f`

### Buttons not responding

1. Check GPIO wiring
2. Enable verbose logging: `--verbose`
3. Verify button connections to GND

## License

[MIT](LICENSE) © David Vornholt

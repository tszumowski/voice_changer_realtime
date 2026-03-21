# Voice Changer

Near-real-time voice changer for macOS using [ElevenLabs Speech-to-Speech API](https://elevenlabs.io/docs/api-reference/speech-to-speech) and [BlackHole](https://existential.audio/blackhole/) virtual audio driver.

Speak into your mic, hear your transformed voice through speakers, and route it to any app (Discord, Zoom, Google Meet, etc.) as a virtual microphone.

## Architecture

```
┌─────────────┐     ┌──────────────────────────────────────────────────┐
│  Microphone  │────>│              Python App                         │
└─────────────┘     │                                                  │
                    │  [Capture Thread]  16kHz mono PCM, 30ms frames   │
                    │        │                                         │
                    │        v                                         │
                    │  [VAD Engine]  webrtcvad, detects speech segments │
                    │        │                                         │
                    │        v  (1-1.5s speech segments)               │
                    │  [Transform Thread]  ElevenLabs STS API          │
                    │        │             (streaming response)        │
                    │        v                                         │
                    │  [Playback Thread]  writes to output device      │
                    └────────┬─────────────────────────────────────────┘
                             │
                             v
                    ┌──────────────────┐
                    │ Multi-Output Dev │
                    ├──────────────────┤
                    │ Built-in Output  │──> You hear it (speakers/headphones)
                    │ BlackHole 2ch    │──> Target apps receive it (virtual mic)
                    └──────────────────┘
```

### How It Works

1. **Capture**: PyAudio captures microphone input at 16kHz, mono, 16-bit PCM using a callback that pushes 30ms frames into a queue.

2. **Voice Activity Detection (VAD)**: A `webrtcvad`-based state machine detects when you're speaking. It accumulates speech frames into segments (up to 1.5s by default) and emits them when silence is detected (250ms of silence) or the segment reaches max length.

3. **Transform**: Each speech segment is sent to the ElevenLabs Speech-to-Speech streaming API, which returns the audio re-spoken in the target voice. The streaming endpoint delivers audio chunks as they're generated.

4. **Playback**: Transformed audio chunks are immediately written to the output device (Multi-Output Device), which simultaneously plays through your speakers AND routes to BlackHole for other apps.

### Latency Budget

| Component | Time |
|-----------|------|
| VAD segment accumulation | ~1-1.5s (speech buffer) |
| ElevenLabs API processing | ~1-2s (model dependent) |
| Network round-trip | ~50ms |
| Audio playback buffer | ~30ms |
| **Total (speech end to hearing output)** | **~1-2s** |

> Note: ElevenLabs STS v2 models have inherent processing latency of 1-2s. This is an API-side constraint. The app minimizes all other sources of latency. Reducing `--segment-duration` to 1.0s can help, at the cost of voice quality.

## Prerequisites

- **macOS** (tested on Sonoma/Sequoia, Apple Silicon)
- **Python 3.12+**
- **Homebrew** (for portaudio)
- **ElevenLabs account** with API key
- **BlackHole** virtual audio driver

## Setup

### 1. Install BlackHole

BlackHole is a free, open-source virtual audio driver for macOS.

1. Go to [existential.audio/blackhole](https://existential.audio/blackhole/)
2. Enter your email to receive the download link
3. Download and install the **BlackHole 2ch** `.pkg` file
4. If prompted, allow the extension in **System Settings > Privacy & Security**
5. **Restart your Mac** (required for the audio driver to load)

### 2. Create Multi-Output Device

This lets you hear the transformed audio AND route it to apps simultaneously.

1. Open **Audio MIDI Setup** (press `Cmd+Space`, type "Audio MIDI Setup")
2. Click the **+** button in the bottom-left corner
3. Select **"Create Multi-Output Device"**
4. Check **both**:
   - **Built-in Output** (speakers/headphones) — must be the **top** device (clock source)
   - **BlackHole 2ch**
5. Enable **Drift Correction** on BlackHole 2ch (NOT on Built-in Output)
6. Right-click the Multi-Output Device > **"Use This Device For Sound Output"**

> **Important**: Built-in Output must be the top/primary device. macOS doesn't support volume control on Multi-Output devices — adjust volume in Audio MIDI Setup or use headphones.

### 3. Install the App

```bash
# portaudio is needed for PyAudio (skip if already installed)
brew install portaudio

# Set up environment
cd voice_changer
cp .env.example .env
# Edit .env and add your ELEVENLABS_API_KEY

# Install dependencies
uv sync
```

### 4. Verify Setup

```bash
# List audio devices — confirm BlackHole and Multi-Output Device appear
uv run voice-changer list-devices

# List available voices
uv run voice-changer list-voices
```

Expected output should show BlackHole 2ch and Multi-Output Device with their indices.

## Usage

### Live Mode (Real-Time Voice Changing)

```bash
# Basic — uses default voice and devices
uv run voice-changer live

# Specify a voice and output device
uv run voice-changer live --voice-id CwhRBWXzGAHq8TQ4Fs17 --output-device 5

# With debug logging
uv run voice-changer --verbose live

# Shorter segments for lower latency (at cost of quality)
uv run voice-changer live --segment-duration 1.0

# More aggressive VAD (better at ignoring background noise)
uv run voice-changer live --vad-aggressiveness 3
```

Press `Ctrl+C` to stop. Session stats (segments processed, errors) are printed on exit.

### Test Mode (File-Based, No Mic Needed)

Perfect for verifying the pipeline works without needing audio devices:

```bash
# Transform a WAV file
uv run voice-changer test -i samples/sample.wav -o output.wav

# With a specific voice
uv run voice-changer test -i samples/sample.wav --voice-id pNInz6obpgDQGcFmaJgB
```

### Using with Discord / Zoom / Google Meet

1. Start the voice changer: `uv run voice-changer live --output-device <multi-output-index>`
2. In your target app's audio settings, select **"BlackHole 2ch"** as the **microphone/input device**
3. Speak normally — the app receives your transformed voice

### CLI Reference

```
uv run voice-changer [-h] [--verbose] {list-devices,list-voices,test,live}

Commands:
  list-devices     List available audio devices
  list-voices      List available ElevenLabs voices
  test             File-based E2E test (no mic needed)
  live             Start live voice changing

live options:
  --voice-id ID            Target voice ID (default: first available)
  --input-device N         Input device index (default: system mic)
  --output-device N        Output device index (default: system output)
  --model MODEL            STS model (default: eleven_english_sts_v2)
  --segment-duration SECS  Max speech segment length (default: 1.5)
  --vad-aggressiveness N   1-3, higher = more aggressive (default: 2)

test options:
  -i, --input-file PATH    Input WAV file (required)
  -o, --output-file PATH   Output WAV file (default: output.wav)
  --voice-id ID            Target voice ID
  --model MODEL            STS model
```

## Configuration

### Environment Variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `ELEVENLABS_API_KEY` | Yes | Your ElevenLabs API key |
| `VOICE_ID` | No | Default voice ID to use |

### Audio Format

The app uses **16kHz, mono, 16-bit PCM** throughout the pipeline. This is the optimal format for ElevenLabs STS — no resampling needed, minimal bandwidth.

## Testing

```bash
# Run all unit tests (no API calls, instant)
uv run pytest tests/ -v

# Run only VAD tests
uv run pytest tests/test_vad.py -v

# Run E2E test with mock API (no credits used)
uv run pytest tests/test_e2e.py -v

# Run file-based test with REAL API (costs credits!)
uv run voice-changer test -i samples/sample.wav -o output.wav
```

## Project Structure

```
voice_changer/
├── pyproject.toml              # Project config, dependencies
├── .env                        # API key (gitignored)
├── .env.example                # Template
├── src/voice_changer/
│   ├── cli.py                  # CLI entry point (argparse)
│   ├── config.py               # Settings dataclass, .env loading
│   ├── audio_devices.py        # PyAudio device discovery
│   ├── capture.py              # Mic capture thread
│   ├── vad.py                  # Voice Activity Detection
│   ├── transformer.py          # ElevenLabs STS API wrapper
│   ├── playback.py             # Audio output thread
│   └── pipeline.py             # Orchestrator (live + test modes)
├── tests/
│   ├── test_config.py          # Config loading tests
│   ├── test_vad.py             # VAD state machine tests
│   └── test_e2e.py             # Pipeline tests (mocked API)
└── samples/                    # Test audio files
```

## Troubleshooting

### No BlackHole or Multi-Output Device found
- Did you restart your Mac after installing BlackHole?
- Check Audio MIDI Setup — is BlackHole 2ch listed?
- Re-run the installer if needed

### No audio output / can't hear transformed voice
- Verify `--output-device` points to the Multi-Output Device (use `list-devices` to find the index)
- Make sure Built-in Output is the top device in the Multi-Output Device
- Check that Drift Correction is enabled on BlackHole 2ch

### Audio glitches or crackling
- Enable Drift Correction on all devices except the clock source (top device) in Audio MIDI Setup
- Ensure both devices use the same sample rate
- Try using headphones to reduce feedback

### Feedback loop
- **Use headphones!** If the mic picks up the speaker output, you'll get a feedback loop
- The `remove_background_noise` option (on by default) helps but won't eliminate it

### API errors (401/403)
- Check your API key in `.env`
- Ensure your key has Speech-to-Speech permissions (not restricted)
- Verify your account has sufficient credits

### High latency
- ElevenLabs STS v2 has ~1-2s processing latency — this is API-side
- Reduce `--segment-duration` (e.g., 1.0) for shorter segments
- Use `eleven_english_sts_v2` (English only) which is slightly faster than multilingual
- Ensure you're on a stable, low-latency internet connection

### PyAudio won't install
```bash
brew install portaudio
uv sync
```

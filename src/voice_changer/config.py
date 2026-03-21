"""Configuration: loads .env and provides a Settings dataclass."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    api_key: str
    voice_id: str | None = None
    model_id: str = "eleven_english_sts_v2"
    input_device: int | None = None
    output_device: int | None = None
    sample_rate: int = 16000
    chunk_duration_ms: int = 30
    segment_duration_s: float = 1.0
    output_format: str = "pcm_16000"
    remove_background_noise: bool = True
    vad_aggressiveness: int = 2
    vad_silence_duration_s: float = 0.25
    mode: str = "normal"
    resemble_api_key: str | None = None
    resemble_voice_uuid: str | None = None
    verbose: bool = False

    @property
    def frame_size(self) -> int:
        """Samples per VAD frame (e.g. 480 for 30ms at 16kHz)."""
        return int(self.sample_rate * self.chunk_duration_ms / 1000)

    @property
    def frame_bytes(self) -> int:
        """Bytes per VAD frame (16-bit = 2 bytes/sample)."""
        return self.frame_size * 2

    @property
    def max_segment_frames(self) -> int:
        """Max frames in one segment before forced emission."""
        return int(self.segment_duration_s * 1000 / self.chunk_duration_ms)

    @property
    def silence_frames(self) -> int:
        """Number of consecutive silence frames to end a segment."""
        return int(self.vad_silence_duration_s * 1000 / self.chunk_duration_ms)


def load_settings(**overrides) -> Settings:
    """Load settings from .env file and apply CLI overrides."""
    # Look for .env in project root
    env_path = Path(__file__).parent.parent.parent / ".env"
    load_dotenv(env_path)

    api_key = overrides.pop("api_key", None) or os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise SystemExit(
            "Error: ELEVENLABS_API_KEY not found.\n"
            "Set it in .env or pass --api-key. See .env.example for reference."
        )

    voice_id = overrides.pop("voice_id", None) or os.getenv("VOICE_ID")
    resemble_api_key = overrides.pop("resemble_api_key", None) or os.getenv("RESEMBLE_API_KEY")
    resemble_voice_uuid = overrides.pop("resemble_voice_uuid", None) or os.getenv("RESEMBLE_VOICE_UUID")

    return Settings(
        api_key=api_key,
        voice_id=voice_id,
        resemble_api_key=resemble_api_key,
        resemble_voice_uuid=resemble_voice_uuid,
        **overrides,
    )

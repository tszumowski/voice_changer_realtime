"""ElevenLabs Speech-to-Speech API wrapper."""

from __future__ import annotations

import io
import logging
import struct
import time
from typing import Iterator

from elevenlabs import ElevenLabs

logger = logging.getLogger(__name__)


def create_client(api_key: str) -> ElevenLabs:
    """Create an ElevenLabs API client."""
    return ElevenLabs(api_key=api_key)


def list_voices(client: ElevenLabs) -> list[dict]:
    """List available voices."""
    response = client.voices.get_all()
    return [
        {"voice_id": v.voice_id, "name": v.name, "category": v.category}
        for v in response.voices
    ]


def get_default_voice_id(client: ElevenLabs) -> str:
    """Get the first available voice ID."""
    voices = list_voices(client)
    if not voices:
        raise SystemExit("Error: No voices available on your ElevenLabs account.")
    # Prefer a voice named "Rachel" or "Adam" if available
    for preferred in ["Rachel", "Adam", "Sarah"]:
        for v in voices:
            if v["name"] == preferred:
                return v["voice_id"]
    return voices[0]["voice_id"]


def _wrap_pcm_as_wav(pcm_data: bytes, sample_rate: int = 16000, channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw PCM data in a WAV header for the API."""
    data_size = len(pcm_data)
    file_size = 36 + data_size
    buf = io.BytesIO()
    # RIFF header
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", file_size))
    buf.write(b"WAVE")
    # fmt chunk
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))  # chunk size
    buf.write(struct.pack("<H", 1))  # PCM format
    buf.write(struct.pack("<H", channels))
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * channels * sample_width))  # byte rate
    buf.write(struct.pack("<H", channels * sample_width))  # block align
    buf.write(struct.pack("<H", sample_width * 8))  # bits per sample
    # data chunk
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(pcm_data)
    return buf.getvalue()


def transform_segment(
    client: ElevenLabs,
    pcm_audio: bytes,
    voice_id: str,
    model_id: str = "eleven_english_sts_v2",
    output_format: str = "pcm_16000",
    remove_background_noise: bool = True,
    sample_rate: int = 16000,
) -> Iterator[bytes]:
    """Send a PCM audio segment to ElevenLabs STS and yield output chunks.

    Args:
        client: ElevenLabs API client.
        pcm_audio: Raw PCM 16-bit LE mono audio bytes.
        voice_id: Target voice ID.
        model_id: STS model identifier.
        output_format: Desired output format (e.g. 'pcm_16000').
        remove_background_noise: Whether to remove background noise.
        sample_rate: Sample rate of the input PCM data.

    Yields:
        Chunks of transformed audio bytes.
    """
    start = time.monotonic()
    first_chunk = True

    def _call_api(use_stream: bool = True):
        method = client.speech_to_speech.stream if use_stream else client.speech_to_speech.convert
        return method(
            voice_id=voice_id,
            audio=("segment.pcm", pcm_audio, "audio/raw"),
            model_id=model_id,
            output_format=output_format,
            remove_background_noise=remove_background_noise,
            file_format="pcm_s16le_16",
            optimize_streaming_latency=4,
            voice_settings='{"style":0,"use_speaker_boost":false}',
        )

    try:
        response = _call_api(use_stream=True)
        for chunk in response:
            if first_chunk:
                latency = time.monotonic() - start
                logger.info("API first-byte latency: %.0fms", latency * 1000)
                first_chunk = False
            yield chunk

    except Exception as e:
        logger.error("ElevenLabs API error: %s", e)
        try:
            logger.info("Retrying with convert endpoint...")
            response = _call_api(use_stream=False)
            for chunk in response:
                yield chunk
        except Exception as e2:
            logger.error("API retry failed: %s. Skipping segment.", e2)

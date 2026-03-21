"""End-to-end test for the voice changer pipeline.

NOTE: This test calls the ElevenLabs API and requires:
  1. A valid API key in .env
  2. A sample.wav file in samples/

Run with: uv run pytest tests/test_e2e.py -v
Skip with: uv run pytest tests/ -v -k "not e2e"
"""

import os
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voice_changer.config import Settings
from voice_changer.transformer import _wrap_pcm_as_wav


@pytest.fixture
def settings():
    return Settings(
        api_key="test_key",
        voice_id="test_voice",
        sample_rate=16000,
    )


def test_wrap_pcm_as_wav():
    """Test that PCM data is correctly wrapped in a WAV header."""
    pcm = b"\x00\x01" * 100  # 200 bytes of PCM
    wav = _wrap_pcm_as_wav(pcm, sample_rate=16000)

    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[12:16] == b"fmt "
    assert wav[36:40] == b"data"
    # data chunk size should match PCM size
    assert wav[40:44] == len(pcm).to_bytes(4, "little")
    assert wav[44:] == pcm


@pytest.mark.skipif(
    not os.path.exists("samples/sample.wav"),
    reason="No sample.wav found - run: uv run voice-changer test -i samples/sample.wav first",
)
def test_sample_wav_format():
    """Verify the sample WAV file has the expected format."""
    with wave.open("samples/sample.wav", "rb") as wf:
        assert wf.getnchannels() == 1, "Expected mono"
        assert wf.getsampwidth() == 2, "Expected 16-bit"
        assert wf.getframerate() == 16000, "Expected 16kHz"
        duration = wf.getnframes() / wf.getframerate()
        assert 1.0 < duration < 30.0, f"Unexpected duration: {duration}s"


def test_pipeline_with_mock_api(settings, tmp_path):
    """Test the full pipeline with a mocked ElevenLabs API."""
    import numpy as np

    from voice_changer.pipeline import run_test

    # Create a test WAV with a sine wave (simulates speech)
    sample_rate = 16000
    duration = 2.0
    t = np.arange(int(sample_rate * duration)) / sample_rate
    samples = (np.sin(2 * np.pi * 300 * t) * 16000).astype(np.int16)

    input_path = tmp_path / "test_input.wav"
    with wave.open(str(input_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())

    output_path = tmp_path / "test_output.wav"

    # Mock the ElevenLabs client to return fake audio
    fake_output = (np.sin(2 * np.pi * 200 * t) * 8000).astype(np.int16).tobytes()

    with patch("voice_changer.pipeline.create_client") as mock_create, \
         patch("voice_changer.pipeline.get_default_voice_id", return_value="fake_voice"), \
         patch("voice_changer.pipeline.transform_segment", return_value=iter([fake_output])):

        result = run_test(settings, str(input_path), str(output_path))

    assert result.exists()
    with wave.open(str(result), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        out_duration = wf.getnframes() / wf.getframerate()
        assert out_duration > 0.5, f"Output too short: {out_duration}s"
